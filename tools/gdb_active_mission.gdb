# Linux adapter for the mission behavior also used by windows_debugger.py.
set pagination off
set confirm off
handle SIGSEGV nostop noprint pass
handle SIGBUS nostop noprint pass
handle SIGILL nostop noprint pass
handle SIGFPE nostop noprint pass
handle SIGUSR1 nostop noprint pass
handle SIGUSR2 nostop noprint pass
handle SIGPIPE nostop noprint pass
handle SIGALRM nostop noprint pass
handle SIGQUIT nostop noprint pass
python
import gdb
import sys
sys.path.insert(0, "tools")
from mission_hooks import MissionHooks, MISSION_CONNECT_STATE, STARTUP_STATE_ENTRY, GATE_ENTRY

inferior = gdb.selected_inferior()

class GdbMemory:
    def read(self, address, size):
        return bytes(inferior.read_memory(address, size))

    def write(self, address, data):
        inferior.write_memory(address, data)

hooks = MissionHooks(GdbMemory(), inferior.pid)

def registers():
    return {name: int(gdb.parse_and_eval("$" + ("pc" if name == "rip" else name))) & 0xffffffffffffffff
            for name in ("rcx", "rdx", "rsi", "rsp", "rip")}

class ExecutionHook(gdb.Breakpoint):
    def __init__(self, address, callback):
        super().__init__(f"*{address:#x}", type=gdb.BP_HARDWARE_BREAKPOINT)
        self.callback = callback

    def stop(self):
        self.callback(registers())
        return False

class PlayModeWrite(gdb.Breakpoint):
    def __init__(self, address):
        super().__init__(f"*(int*){address:#x}", type=gdb.BP_WATCHPOINT, wp_class=gdb.WP_WRITE)

    def stop(self):
        hooks.playmode_write(registers())
        return False

hooks.log(f"attaché, GameStartingInfo {hooks.starting_info()}")
PlayModeWrite(hooks.starting_info()[0])
ExecutionHook(MISSION_CONNECT_STATE, hooks.connect)
ExecutionHook(STARTUP_STATE_ENTRY, hooks.startup)
ExecutionHook(GATE_ENTRY, hooks.gate)
end
continue
