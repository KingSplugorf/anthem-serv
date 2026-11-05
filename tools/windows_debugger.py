"""Experimental x64 Windows replacement for gdb_active_mission.gdb.

Uses the same three execution breakpoints and one four-byte write watchpoint.
Requires 64-bit CPython and the repository's exact game build at image base
0x140000000. Reads 'stop' from stdin to clear breakpoints and detach gracefully.
"""
import ctypes as C
import os
from pathlib import Path
import struct
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mission_hooks import MissionHooks, MISSION_CONNECT_STATE, STARTUP_STATE_ENTRY, GATE_ENTRY
from process_memory import open_memory, read_at
from tls_client_diagnostics import TlsFailureHooks, TLS_FAILURE_SITE
from windows_memory import kernel, check, modules

DWORD, QWORD, HANDLE = C.c_uint32, C.c_uint64, C.c_void_p
CONTINUE, NOT_HANDLED = 0x00010002, 0x80010001
SINGLE_STEP, BREAKPOINT = 0x80000004, 0x80000003


class ExceptionRecord(C.Structure):
    _fields_ = [("code", DWORD), ("flags", DWORD), ("record", HANDLE),
                ("address", HANDLE), ("count", DWORD), ("alignment", DWORD),
                ("information", QWORD * 15)]


class ExceptionInfo(C.Structure):
    _fields_ = [("record", ExceptionRecord), ("first_chance", DWORD)]


class CreateThread(C.Structure):
    _fields_ = [("thread", HANDLE), ("tls", HANDLE), ("start", HANDLE)]


class CreateProcess(C.Structure):
    _fields_ = [("file", HANDLE), ("process", HANDLE), ("thread", HANDLE),
                ("base", HANDLE), ("debug_offset", DWORD), ("debug_size", DWORD),
                ("tls", HANDLE), ("start", HANDLE), ("image_name", HANDLE), ("unicode", DWORD)]


class LoadDll(C.Structure):
    _fields_ = [("file", HANDLE), ("base", HANDLE), ("debug_offset", DWORD),
                ("debug_size", DWORD), ("image_name", HANDLE), ("unicode", DWORD)]


class EventInfo(C.Union):
    _fields_ = [("exception", ExceptionInfo), ("thread", CreateThread),
                ("process", CreateProcess), ("dll", LoadDll)]


class Event(C.Structure):
    _fields_ = [("code", DWORD), ("pid", DWORD), ("tid", DWORD), ("info", EventInfo)]


class Context:
    """Windows AMD64 CONTEXT, explicitly aligned to 16 bytes (1232-byte ABI)."""
    OFFSETS = dict(dr0=72, dr1=80, dr2=88, dr3=96, dr6=104, dr7=112,
                   rcx=128, rdx=136, rbx=144, rsp=152, rsi=168, r12=216, rip=248)

    def __init__(self):
        self.storage = C.create_string_buffer(1232 + 15)
        self.address = (C.addressof(self.storage) + 15) & ~15
        self.buffer = (C.c_ubyte * 1232).from_address(self.address)
        struct.pack_into("<I", self.buffer, 48, 0x100013)  # control, integer, debug registers

    def get(self, name):
        return struct.unpack_from("<Q", self.buffer, self.OFFSETS[name])[0]

    def set(self, name, value):
        struct.pack_into("<Q", self.buffer, self.OFFSETS[name], value)

    def resume(self):
        flags = struct.unpack_from("<I", self.buffer, 68)[0]
        struct.pack_into("<I", self.buffer, 68, flags | 0x10000)  # RF: don't retrigger execution BP
        self.set("dr6", 0)


def configure(context, addresses, *, watchpoint=3):
    if not 1 <= len(addresses) <= 4:
        raise ValueError("Expected one to four hardware breakpoints")
    if watchpoint is not None and (not 0 <= watchpoint < len(addresses) or addresses[watchpoint] % 4):
        raise ValueError("Four-byte watchpoint must exist and be aligned")
    if context.get("dr7") & 0xff:
        raise OSError("Thread already has hardware breakpoints; refusing to replace them")
    for index in range(4):
        context.set(f"dr{index}", addresses[index] if index < len(addresses) else 0)
    context.set("dr6", 0)
    control = sum(1 << (index * 2) for index in range(len(addresses)))
    if watchpoint is not None:
        control |= 0xD << (16 + watchpoint * 4)  # write watch, length four
    context.set("dr7", control)


class AddressSpace:
    def __init__(self, memory):
        self.memory = memory

    def read(self, address, size):
        return read_at(self.memory, address, size)

    def write(self, address, data):
        self.memory.seek(address)
        self.memory.write(data)


def debug_api():
    k = kernel()
    for name, (args, result) in {
        "DebugActiveProcess": ([DWORD], C.c_int32),
        "DebugActiveProcessStop": ([DWORD], C.c_int32),
        "DebugSetProcessKillOnExit": ([C.c_int32], C.c_int32),
        "WaitForDebugEvent": ([C.POINTER(Event), DWORD], C.c_int32),
        "ContinueDebugEvent": ([DWORD, DWORD, DWORD], C.c_int32),
        "GetThreadContext": ([HANDLE, C.c_void_p], C.c_int32),
        "SetThreadContext": ([HANDLE, C.c_void_p], C.c_int32),
        "SuspendThread": ([HANDLE], DWORD), "ResumeThread": ([HANDLE], DWORD),
        "GetExitCodeThread": ([HANDLE, C.POINTER(DWORD)], C.c_int32),
        "GetCurrentProcess": ([], HANDLE),
        "DuplicateHandle": ([HANDLE, HANDLE, HANDLE, C.POINTER(HANDLE), DWORD, C.c_int32, DWORD], C.c_int32),
    }.items():
        fn = getattr(k, name)
        fn.argtypes, fn.restype = args, result
    return k


def run(pid, stop, *, tls_diagnostics=False):
    if os.name != "nt":
        raise OSError("Native Windows debugger requires 64-bit Windows")
    image_base, image_path = next(modules(pid))
    if image_base != 0x140000000 or Path(image_path).name.lower() not in ("anthem.exe", "dylan.main_win64_retail.exe"):
        raise OSError("Unsupported game image/base; fixed-address patches cannot be applied safely")
    k = debug_api()
    threads, originals = {}, {}
    attached = False
    with open_memory(pid, "rb" if tls_diagnostics else "r+b") as memory:
        if tls_diagnostics:
            hooks = TlsFailureHooks(AddressSpace(memory), pid)
            hooks.validate(image_path)
            addresses, callbacks = (TLS_FAILURE_SITE,), (hooks.failure,)
        else:
            hooks = MissionHooks(AddressSpace(memory), pid)
            pointer, _ = hooks.starting_info()
            if pointer % 4:
                raise OSError("GameStartingInfo watchpoint is not aligned")
            addresses = (MISSION_CONNECT_STATE, STARTUP_STATE_ENTRY, GATE_ENTRY, pointer)
            callbacks = (hooks.connect, hooks.startup, hooks.gate, hooks.playmode_write)
        try:
            check(k.DebugActiveProcess(pid))
            attached = True
            check(k.DebugSetProcessKillOnExit(False))
            initial_breakpoint = True
            if tls_diagnostics:
                hooks.log("attached for certificate diagnostics; mission hooks inactive; retry the game connection")
            else:
                hooks.log(f"attaché (Win32), GameStartingInfo {hooks.starting_info()}")
            while not stop.is_set():
                event = Event()
                if not k.WaitForDebugEvent(C.byref(event), 100):
                    if C.get_last_error() == 121:
                        continue
                    check(False)
                disposition = CONTINUE
                try:
                    if event.code in (2, 3):  # CREATE_THREAD / CREATE_PROCESS
                        borrowed = event.info.thread.thread if event.code == 2 else event.info.process.thread
                        # ContinueDebugEvent owns the event's original thread/process handles.
                        # Duplicate the thread handle so our cleanup never double-closes them.
                        duplicate = HANDLE()
                        owner = k.GetCurrentProcess()
                        check(k.DuplicateHandle(owner, borrowed, owner, C.byref(duplicate), 0, False, 2))
                        handle = duplicate.value
                        threads[event.tid] = handle
                        if event.code == 3 and event.info.process.file:
                            check(k.CloseHandle(event.info.process.file))
                        context = Context()
                        check(k.GetThreadContext(handle, context.address))
                        originals[event.tid] = {name: context.get(name) for name in ("dr0", "dr1", "dr2", "dr3", "dr6", "dr7")}
                        configure(context, addresses, watchpoint=None if tls_diagnostics else 3)
                        check(k.SetThreadContext(handle, context.address))
                    elif event.code == 4:  # EXIT_THREAD
                        handle = threads.pop(event.tid, None)
                        originals.pop(event.tid, None)
                        if handle: check(k.CloseHandle(handle))
                    elif event.code == 5:  # EXIT_PROCESS
                        stop.set()
                        attached = False
                    elif event.code == 6:  # LOAD_DLL; debugger owns the supplied file handle
                        if event.info.dll.file: check(k.CloseHandle(event.info.dll.file))
                    elif event.code == 1:
                        code = event.info.exception.record.code
                        disposition = NOT_HANDLED
                        if code == BREAKPOINT and initial_breakpoint:
                            initial_breakpoint = False
                            if tls_diagnostics:
                                hooks.mark_ready()
                            disposition = CONTINUE
                        elif code == SINGLE_STEP and event.tid in threads:
                            context = Context()
                            check(k.GetThreadContext(threads[event.tid], context.address))
                            hits = context.get("dr6") & ((1 << len(callbacks)) - 1)
                            if hits:
                                registers = {name: context.get(name) for name in ("rcx", "rdx", "rbx", "rsi", "r12", "rsp", "rip")}
                                disposition = CONTINUE
                                try:
                                    for index, callback in enumerate(callbacks):
                                        if hits & (1 << index): callback(registers)
                                finally:
                                    context.resume()
                                    check(k.SetThreadContext(threads[event.tid], context.address))
                finally:
                    check(k.ContinueDebugEvent(event.pid, event.tid, disposition))
        finally:
            errors = []
            if attached:
                for tid, handle in threads.items():
                    suspended = False
                    try:
                        status = DWORD()
                        check(k.GetExitCodeThread(handle, C.byref(status)))
                        if status.value != 259: continue
                        if k.SuspendThread(handle) == 0xffffffff: check(False)
                        suspended = True
                        context = Context()
                        check(k.GetThreadContext(handle, context.address))
                        for name, value in originals.get(tid, {}).items(): context.set(name, value)
                        check(k.SetThreadContext(handle, context.address))
                    except OSError as error:
                        errors.append(str(error))
                    finally:
                        if suspended and k.ResumeThread(handle) == 0xffffffff:
                            errors.append("Could not resume debugger thread")
                if not k.DebugActiveProcessStop(pid): errors.append(str(C.WinError(C.get_last_error())))
            for handle in threads.values():
                if not k.CloseHandle(handle): errors.append("Could not close debugger thread handle")
            if errors:
                raise OSError("Debugger cleanup failed: " + "; ".join(errors))


def main():
    stop = threading.Event()
    def listen():
        for line in sys.stdin:
            if line.strip() == "stop":
                stop.set()
                return
        stop.set()  # parent exited; detach instead of leaving the game debugged
    threading.Thread(target=listen, daemon=True).start()
    try:
        run(int(sys.argv[1]), stop, tls_diagnostics=os.environ.get("ANTHEM_TLS_DIAGNOSTICS") == "1")
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
