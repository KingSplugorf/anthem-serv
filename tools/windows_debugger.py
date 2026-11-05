"""Experimental x64 Windows replacement for gdb_active_mission.gdb.

Uses a connection breakpoint while idle; mission selection arms the dependent
execution breakpoints and four-byte write watchpoint until mission startup.
Requires 64-bit CPython and the repository's exact game build at image base
0x140000000. Reads 'stop' from stdin to clear breakpoints and detach gracefully.
"""
import ctypes as C
import argparse
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import struct
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mission_hooks import MissionHooks, MISSION_CONNECT_STATE, STARTUP_STATE_ENTRY, GATE_ENTRY, MISSION_DEBUGGER_READY
from process_memory import open_memory, read_at
from tls_client_diagnostics import TlsFailureHooks, TLS_FAILURE_SITE
from windows_memory import kernel, check, modules
from mission_lifecycle import RepeatingMissionHooks, LEAVE_SUCCESS, STORY_SUCCESS_CALLBACK

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
                   rcx=128, rdx=136, rbx=144, rsp=152, rsi=168, r8=184, r9=192,
                   r12=216, rip=248)

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


def replace_configuration(context, old_addresses, addresses, *, old_watchpoint, watchpoint):
    """Replace only a configuration that this debugger still owns."""
    expected, replacement = Context(), Context()
    configure(expected, old_addresses, watchpoint=old_watchpoint)
    configure(replacement, addresses, watchpoint=watchpoint)
    names = ('dr0', 'dr1', 'dr2', 'dr3')
    # Ignore reserved/debug-control bits, including the CPU's fixed DR7 bit 10.
    mask = 0xffff00ff
    if (context.get('dr7') & mask != expected.get('dr7') & mask or
            any(context.get(name) != expected.get(name) for name in names)):
        raise OSError('Thread debug registers changed outside this debugger; refusing to replace them')
    for name in (*names, 'dr6', 'dr7'):
        context.set(name, replacement.get(name))


def mission_breakpoints(hooks):
    if isinstance(hooks, RepeatingMissionHooks) and hooks.phase == 'active':
        return (MISSION_CONNECT_STATE, LEAVE_SUCCESS, STORY_SUCCESS_CALLBACK), None
    # The dependent handlers do nothing until a mission has been selected.
    # In particular, an idle PlayMode write watch can stop every game thread
    # for writes that never need a Python callback in the hub.
    if hooks.launched is None:
        return (MISSION_CONNECT_STATE,), None
    try:
        pointer, _ = hooks.starting_info()
    except OSError:
        if isinstance(hooks, RepeatingMissionHooks):
            return (MISSION_CONNECT_STATE, STARTUP_STATE_ENTRY, GATE_ENTRY), None
        raise
    if pointer % 4:
        raise OSError('GameStartingInfo watchpoint is not aligned')
    return (MISSION_CONNECT_STATE, STARTUP_STATE_ENTRY, GATE_ENTRY, pointer), 3


@contextmanager
def frozen_threads(k, threads):
    """Pause only owned debuggee threads; undo every successful suspension."""
    suspended = []
    try:
        for handle in list(threads.values()):
            status = DWORD()
            check(k.GetExitCodeThread(handle, C.byref(status)))
            if status.value != 259:
                continue
            if k.SuspendThread(handle) == 0xffffffff:
                check(k.GetExitCodeThread(handle, C.byref(status)))
                if status.value != 259:
                    continue
                check(False)
            suspended.append(handle)
        yield
    finally:
        errors = []
        for handle in reversed(suspended):
            if k.ResumeThread(handle) == 0xffffffff:
                errors.append(str(handle))
        if errors:
            raise OSError('Could not resume mission threads: ' + ', '.join(errors))


def reconfigure_threads(k, threads, addresses, desired, watchpoint, desired_watchpoint, current=None):
    for tid, handle in threads.items():
        status = DWORD()
        check(k.GetExitCodeThread(handle, C.byref(status)))
        if status.value != 259:
            continue
        context = current[1] if current is not None and tid == current[0] else Context()
        if current is None or context is not current[1]:
            check(k.GetThreadContext(handle, context.address))
        replace_configuration(context, addresses, desired, old_watchpoint=watchpoint, watchpoint=desired_watchpoint)
        if current is None or context is not current[1]:
            check(k.SetThreadContext(handle, context.address))


class AddressSpace:
    def __init__(self, memory):
        self.memory = memory

    def read(self, address, size):
        return read_at(self.memory, address, size)

    def write(self, address, data):
        self.memory.seek(address)
        self.memory.write(data)

    def allocate(self, size):
        return self.memory.allocate(size)

    def protect_allocation(self, address, size, protection):
        self.memory.protect_allocation(address, size, protection)

    def release(self, address):
        self.memory.release(address)


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


def run(pid, stop, *, tls_diagnostics=False, ca_certificate=None, mission_session=None):
    if os.name != "nt":
        raise OSError("Native Windows debugger requires 64-bit Windows")
    if tls_diagnostics and ca_certificate is not None:
        raise ValueError('Choose CA registration or passive TLS diagnostics for this debugger session')
    if mission_session is not None and (tls_diagnostics or ca_certificate is not None):
        raise ValueError('Repeating missions require their own debugger session')
    image_base, image_path = next(modules(pid))
    if image_base != 0x140000000 or Path(image_path).name.lower() not in ("anthem.exe", "dylan.main_win64_retail.exe"):
        raise OSError("Unsupported game image/base; fixed-address patches cannot be applied safely")
    k = debug_api()
    threads, originals = {}, {}
    attached = process_exited = False
    ca_mode = ca_certificate is not None
    special_mode = tls_diagnostics or ca_mode
    with open_memory(pid, "rb" if tls_diagnostics else "r+b") as memory, ExitStack() as cleanup:
        if ca_mode:
            from windows_ca_loader import LocalCaHooks
            hooks = LocalCaHooks(AddressSpace(memory), pid, ca_certificate)
            cleanup.callback(lambda: hooks.close(release_unused=not attached, process_exited=process_exited))
            hooks.validate(image_path)
            hooks.prepare()
            addresses, callbacks = hooks.addresses, ()
        elif tls_diagnostics:
            hooks = TlsFailureHooks(AddressSpace(memory), pid)
            hooks.validate(image_path)
            addresses, callbacks = (TLS_FAILURE_SITE,), (hooks.failure,)
        else:
            if mission_session is not None:
                hooks = RepeatingMissionHooks(AddressSpace(memory), pid, mission_session,
                    persona=int(os.environ.get('JOUEUR_ID', '1000')))
                cleanup.callback(hooks.lease.close)
            else:
                hooks = MissionHooks(AddressSpace(memory), pid)
            addresses, watchpoint = mission_breakpoints(hooks)
            callbacks = (hooks.connect, hooks.startup, hooks.gate, hooks.playmode_write)
        if special_mode:
            watchpoint = None
        try:
            check(k.DebugActiveProcess(pid))
            attached = True
            check(k.DebugSetProcessKillOnExit(False))
            initial_breakpoint = True
            if ca_mode:
                hooks.log('attached', 'attached for public CA registration; waiting for debugger readiness')
            elif tls_diagnostics:
                hooks.log("attached for certificate diagnostics; mission hooks inactive; retry the game connection")
            else:
                # GameStartingInfo can disappear while the hub/level changes.
                # The idle breakpoint plan needs no startup object or data watch.
                hooks.log("attaché (Win32) : attente d'un lancement de mission")
            while not stop.is_set():
                event = Event()
                if not k.WaitForDebugEvent(C.byref(event), 100):
                    if C.get_last_error() == 121:
                        if isinstance(hooks, RepeatingMissionHooks) and hooks.has_updates():
                            with frozen_threads(k, threads):
                                hooks.poll()
                                desired, desired_watchpoint = mission_breakpoints(hooks)
                                if desired != addresses or desired_watchpoint != watchpoint:
                                    reconfigure_threads(k, threads, addresses, desired, watchpoint, desired_watchpoint)
                                    addresses, watchpoint = desired, desired_watchpoint
                        continue
                    check(False)
                disposition = CONTINUE
                try:
                    if isinstance(hooks, RepeatingMissionHooks) and event.code != 5:
                        hooks.poll()
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
                        configure(context, addresses, watchpoint=watchpoint)
                        check(k.SetThreadContext(handle, context.address))
                    elif event.code == 4:  # EXIT_THREAD
                        handle = threads.pop(event.tid, None)
                        originals.pop(event.tid, None)
                        if handle: check(k.CloseHandle(handle))
                    elif event.code == 5:  # EXIT_PROCESS
                        stop.set()
                        attached = False
                        process_exited = True
                    elif event.code == 6:  # LOAD_DLL; debugger owns the supplied file handle
                        if event.info.dll.file: check(k.CloseHandle(event.info.dll.file))
                    elif event.code == 1:
                        code = event.info.exception.record.code
                        disposition = NOT_HANDLED
                        if code == BREAKPOINT and initial_breakpoint:
                            initial_breakpoint = False
                            if special_mode:
                                hooks.mark_ready()
                            elif isinstance(hooks, RepeatingMissionHooks):
                                hooks.mark_ready()
                            else:
                                hooks.log(MISSION_DEBUGGER_READY)
                            disposition = CONTINUE
                        elif code == SINGLE_STEP and event.tid in threads:
                            context = Context()
                            check(k.GetThreadContext(threads[event.tid], context.address))
                            hits = context.get("dr6") & ((1 << len(addresses)) - 1)
                            if hits:
                                registers = {name: context.get(name) for name in ("rcx", "rdx", "rbx", "rsi", "r12", "rsp", "rip")}
                                disposition = CONTINUE
                                try:
                                    if ca_mode:
                                        hooks.handle(context, event.tid)
                                        if hooks.completed: stop.set()
                                    else:
                                        if special_mode:
                                            for index, callback in enumerate(callbacks):
                                                if hits & (1 << index): callback(registers)
                                        else:
                                            execution = {MISSION_CONNECT_STATE: hooks.connect, STARTUP_STATE_ENTRY: hooks.startup,
                                                         GATE_ENTRY: hooks.gate}
                                            if isinstance(hooks, RepeatingMissionHooks):
                                                execution[LEAVE_SUCCESS] = hooks.leave
                                                execution[STORY_SUCCESS_CALLBACK] = hooks.leave
                                            for index, address in enumerate(addresses):
                                                if hits & (1 << index):
                                                    (hooks.playmode_write if index == watchpoint else execution[address])(registers)
                                    if not special_mode:
                                        desired, desired_watchpoint = mission_breakpoints(hooks)
                                        if desired != addresses or desired_watchpoint != watchpoint:
                                            # A debug event suspends the process. Update every live
                                            # thread before resuming the mission transition.
                                            reconfigure_threads(k, threads, addresses, desired, watchpoint, desired_watchpoint,
                                                                (event.tid, context))
                                            addresses, watchpoint = desired, desired_watchpoint
                                finally:
                                    context.resume()
                                    check(k.SetThreadContext(threads[event.tid], context.address))
                    if not special_mode and event.code != 5:
                        desired, desired_watchpoint = mission_breakpoints(hooks)
                        if desired != addresses or desired_watchpoint != watchpoint:
                            reconfigure_threads(k, threads, addresses, desired, watchpoint, desired_watchpoint)
                            addresses, watchpoint = desired, desired_watchpoint
                finally:
                    check(k.ContinueDebugEvent(event.pid, event.tid, disposition))
        finally:
            errors = []
            if isinstance(hooks, RepeatingMissionHooks):
                try:
                    if attached and not process_exited:
                        with frozen_threads(k, threads):
                            hooks.close()
                    else:
                        hooks.close(process_exited=process_exited)
                except OSError as error:
                    errors.append(f'Mission cleanup: {error}')
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
                else: attached = False
            for handle in threads.values():
                if not k.CloseHandle(handle): errors.append("Could not close debugger thread handle")
            if errors:
                raise OSError("Debugger cleanup failed: " + "; ".join(errors))
        if ca_mode:
            hooks.report['detached'] = not attached
            hooks.save()
            return hooks.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pid', type=int)
    parser.add_argument('--ca-certificate', type=Path,
                        help='Experimental build-specific registration of this public PEM before a local TLS connect')
    parser.add_argument('--mission-session', help='Run ID for repeatable solo expedition supervision')
    args = parser.parse_args()
    stop = threading.Event()
    def listen():
        for line in sys.stdin:
            if line.strip() == "stop":
                stop.set()
                return
        stop.set()  # parent exited; detach instead of leaving the game debugged
    threading.Thread(target=listen, daemon=True).start()
    try:
        result = run(args.pid, stop, tls_diagnostics=os.environ.get("ANTHEM_TLS_DIAGNOSTICS") == "1",
                     ca_certificate=args.ca_certificate, mission_session=args.mission_session)
        if args.ca_certificate is not None and not result.get('loaded'):
            parser.exit(1, 'CA registration did not complete successfully; inspect data/local_ca_PID.json\n')
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
