"""Native Win32 access. No Wine, /proc emulation, or third-party bindings."""
import ctypes as C
from ctypes import wintypes as W
import io
import threading

MEM_COMMIT = 0x1000
PAGE_GUARD, PAGE_NOACCESS = 0x100, 0x01
WRITABLE = {0x04, 0x08, 0x40, 0x80}
PROCESS_VM_OPERATION, PROCESS_VM_READ, PROCESS_VM_WRITE = 0x08, 0x10, 0x20
PROCESS_QUERY_INFORMATION, PROCESS_QUERY_LIMITED_INFORMATION = 0x400, 0x1000
SYNCHRONIZE = 0x100000
DWORD, WORD, BOOL = C.c_uint32, C.c_uint16, C.c_int32
SIZE = C.c_size_t


class MEMORY_BASIC_INFORMATION(C.Structure):
    _fields_ = [("BaseAddress", C.c_void_p), ("AllocationBase", C.c_void_p),
                ("AllocationProtect", DWORD), ("PartitionId", WORD),
                ("RegionSize", SIZE), ("State", DWORD),
                ("Protect", DWORD), ("Type", DWORD)]


class MODULEENTRY32W(C.Structure):
    _fields_ = [("dwSize", DWORD), ("th32ModuleID", DWORD),
                ("th32ProcessID", DWORD), ("GlblcntUsage", DWORD),
                ("ProccntUsage", DWORD), ("modBaseAddr", C.c_void_p),
                ("modBaseSize", DWORD), ("hModule", W.HMODULE),
                ("szModule", W.WCHAR * 256), ("szExePath", W.WCHAR * 260)]


def kernel():
    if C.sizeof(C.c_void_p) != 8:
        raise OSError("Use 64-bit Python to patch the 64-bit game")
    k = C.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "OpenProcess": ([DWORD, BOOL, DWORD], W.HANDLE),
        "CloseHandle": ([W.HANDLE], BOOL),
        "ReadProcessMemory": ([W.HANDLE, C.c_void_p, C.c_void_p, SIZE, C.POINTER(SIZE)], BOOL),
        "WriteProcessMemory": ([W.HANDLE, C.c_void_p, C.c_void_p, SIZE, C.POINTER(SIZE)], BOOL),
        "VirtualProtectEx": ([W.HANDLE, C.c_void_p, SIZE, DWORD, C.POINTER(DWORD)], BOOL),
        "VirtualAllocEx": ([W.HANDLE, C.c_void_p, SIZE, DWORD, DWORD], C.c_void_p),
        "VirtualFreeEx": ([W.HANDLE, C.c_void_p, SIZE, DWORD], BOOL),
        "VirtualQueryEx": ([W.HANDLE, C.c_void_p, C.POINTER(MEMORY_BASIC_INFORMATION), SIZE], SIZE),
        "FlushInstructionCache": ([W.HANDLE, C.c_void_p, SIZE], BOOL),
        "WaitForSingleObject": ([W.HANDLE, DWORD], DWORD),
        "CreateToolhelp32Snapshot": ([DWORD, DWORD], W.HANDLE),
        "Module32FirstW": ([W.HANDLE, C.POINTER(MODULEENTRY32W)], BOOL),
        "Module32NextW": ([W.HANDLE, C.POINTER(MODULEENTRY32W)], BOOL),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(k, name)
        fn.argtypes, fn.restype = args, result
    return k


def last_error():
    return C.get_last_error() if hasattr(C, "get_last_error") else 0


def win_error():
    code = last_error()
    return C.WinError(code) if hasattr(C, "WinError") else OSError(code, "Win32 call failed")


def check(ok):
    if not ok:
        raise win_error()


class Memory(io.RawIOBase):
    def __init__(self, pid, writable=True, api=None):
        super().__init__()
        self.api = api if api is not None else kernel()
        access = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        if writable:
            access |= PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        self.handle = self.api.OpenProcess(access, False, pid)
        check(self.handle)
        self.can_write = writable
        self.position = 0
        self.lock = threading.RLock()
        self.allocations = {}

    def close(self):
        if not self.closed:
            try:
                if getattr(self, "handle", None):
                    check(self.api.CloseHandle(self.handle))
                    self.handle = None
            finally:
                super().close()

    def _range(self, address, size):
        if self.closed:
            raise ValueError("I/O operation on closed process")
        if size < 0 or address < 0x10000 or address + size > 1 << 47:
            raise OSError(f"invalid process memory range {address:#x}+{size:#x}")

    def seek(self, offset, whence=0):
        with self.lock:
            if whence not in (0, 1):
                raise ValueError("process memory has no SEEK_END")
            position = offset + (self.position if whence else 0)
            if position < 0:
                raise ValueError("negative memory address")
            self.position = position
            return position

    def tell(self):
        return self.position

    def read_at(self, address, size):
        self._range(address, size)
        if not size:
            return b""
        data, count = C.create_string_buffer(size), SIZE()
        check(self.api.ReadProcessMemory(self.handle, address, data, size, C.byref(count)))
        if count.value != size:
            raise OSError(f"partial process read at {address:#x}: {count.value}/{size}")
        return data.raw

    def read(self, size=-1):
        with self.lock:
            data = self.read_at(self.position, size)
            self.position += len(data)
            return data

    def allocate(self, size):
        if not self.can_write:
            raise io.UnsupportedOperation("process opened read-only")
        if not 0 < size <= 0x1000000:
            raise ValueError("Invalid remote allocation size")
        address = self.api.VirtualAllocEx(self.handle, None, size, 0x3000, 0x04)
        check(address)
        self.allocations[address] = size
        return address

    def protect_allocation(self, address, size, protection):
        if not self.can_write:
            raise io.UnsupportedOperation("process opened read-only")
        if protection not in (0x04, 0x20) or not 0 < size <= self.allocations.get(address, 0):
            raise ValueError("Expected an owned allocation and read/write or execute/read protection")
        previous = DWORD()
        check(self.api.VirtualProtectEx(self.handle, address, size, protection, C.byref(previous)))
        check(self.api.FlushInstructionCache(self.handle, address, size))

    def release(self, address):
        if address not in self.allocations:
            raise ValueError("Remote allocation is not owned by this handle")
        check(self.api.VirtualFreeEx(self.handle, address, 0, 0x8000))
        del self.allocations[address]

    def write(self, data):
        data = bytes(data)
        with self.lock:
            self._range(self.position, len(data))
            if not self.can_write:
                raise io.UnsupportedOperation("process opened read-only")
            if not data:
                return 0
            start, end = self.position, self.position + len(data)
            changed = []
            try:
                cursor = start
                while cursor < end:
                    info = MEMORY_BASIC_INFORMATION()
                    check(self.api.VirtualQueryEx(self.handle, cursor, C.byref(info), C.sizeof(info)))
                    limit = min(end, (info.BaseAddress or 0) + info.RegionSize)
                    if info.State != MEM_COMMIT or info.Protect & PAGE_GUARD or info.Protect & 0xff == PAGE_NOACCESS or limit <= cursor:
                        raise OSError(f"uncommitted or inaccessible write at {cursor:#x}")
                    if info.Protect & 0xff not in WRITABLE:
                        old = DWORD()
                        check(self.api.VirtualProtectEx(self.handle, cursor, limit-cursor, 0x40, C.byref(old)))
                        changed.append((cursor, limit-cursor, old.value))
                    cursor = limit
                count = SIZE()
                check(self.api.WriteProcessMemory(self.handle, start, data, len(data), C.byref(count)))
                if count.value != len(data):
                    raise OSError(f"partial process write at {start:#x}")
                check(self.api.FlushInstructionCache(self.handle, start, len(data)))
            finally:
                for address, size, protection in reversed(changed):
                    previous = DWORD()
                    check(self.api.VirtualProtectEx(self.handle, address, size, protection, C.byref(previous)))
            self.position = end
            return len(data)


def regions(pid):
    with Memory(pid, writable=False) as memory:
        cursor = 0x10000
        while cursor < 1 << 47:
            info = MEMORY_BASIC_INFORMATION()
            if not memory.api.VirtualQueryEx(memory.handle, cursor, C.byref(info), C.sizeof(info)):
                # ERROR_INVALID_PARAMETER marks the end of the process address space.
                if last_error() == 87:
                    break
                check(False)
            start = info.BaseAddress or 0
            end = start + info.RegionSize
            if end <= cursor:
                raise OSError("VirtualQueryEx did not advance")
            if info.State == MEM_COMMIT and not info.Protect & PAGE_GUARD and info.Protect & 0xff in WRITABLE:
                yield start, min(end, 1 << 47)
            cursor = end


def modules(pid):
    k = kernel()
    snapshot = k.CreateToolhelp32Snapshot(0x08 | 0x10, pid)
    if snapshot == C.c_void_p(-1).value:
        check(False)
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = C.sizeof(entry)
        check(k.Module32FirstW(snapshot, C.byref(entry)))
        while True:
            yield entry.modBaseAddr, entry.szExePath
            if not k.Module32NextW(snapshot, C.byref(entry)):
                if last_error() != 18:  # ERROR_NO_MORE_FILES
                    check(False)
                break
    finally:
        check(k.CloseHandle(snapshot))


def process_alive(pid):
    k = kernel()
    handle = k.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        if last_error() == 87:  # no such process; access denied is not proof of exit
            return False
        check(False)
    try:
        result = k.WaitForSingleObject(handle, 0)
        if result == 0xffffffff:
            check(False)
        return result == 258
    finally:
        check(k.CloseHandle(handle))
