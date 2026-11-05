import ctypes as C
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import process_memory
import windows_memory as win
import windows_debugger as debugger
from mission_hooks import MissionHooks, STARTING_INFO_POINTER


class FakeKernel:
    def __init__(self):
        self.data = {}
        self.protection = {0x1000: 0x20, 0x2000: 0x02}
        self.calls = []
        self.partial_read = False
        self.fail_write = False

    def OpenProcess(self, access, inherit, pid):
        return 0x123456789  # catch accidental 32-bit handle assumptions

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return 1

    def ReadProcessMemory(self, handle, address, buffer, size, count):
        data = bytes(self.data.get(address+i, 0) for i in range(size))
        C.memmove(buffer, data, size)
        count._obj.value = size - int(self.partial_read)
        return 1

    def VirtualQueryEx(self, handle, address, info, size):
        info._obj.BaseAddress = address & ~0xfff
        info._obj.RegionSize = 4096
        info._obj.State = win.MEM_COMMIT
        info._obj.Protect = self.protection.get(address & ~0xfff, 0x04)
        return C.sizeof(win.MEMORY_BASIC_INFORMATION)

    def VirtualProtectEx(self, handle, address, size, protection, previous):
        page = address & ~0xfff
        previous._obj.value = self.protection[page]
        self.protection[page] = protection
        self.calls.append(("protect", address, size, protection))
        return 1

    def WriteProcessMemory(self, handle, address, data, size, count):
        if self.fail_write:
            return 0
        self.data.update((address+i, value) for i, value in enumerate(data))
        count._obj.value = size
        return 1

    def FlushInstructionCache(self, handle, address, size):
        self.calls.append(("flush", address, size))
        return 1

    def VirtualAllocEx(self, handle, hint, size, flags, protection):
        self.calls.append(('allocate',size,flags,protection))
        self.protection.update((address,protection) for address in range(0x50000,0x50000+size,4096))
        return 0x50000

    def VirtualFreeEx(self, handle, address, size, flags):
        self.calls.append(('release',address,size,flags))
        return 1


class MemoryTests(unittest.TestCase):
    def test_owned_remote_allocation_uses_rw_then_rx_and_explicit_release(self):
        api=FakeKernel()
        with win.Memory(123,api=api) as memory:
            address=memory.allocate(8192)
            memory.protect_allocation(address,4096,0x20)
            self.assertEqual(api.protection[address],0x20)
            self.assertEqual(api.protection[address+4096],0x04)
            with self.assertRaises(ValueError): memory.protect_allocation(address,4096,0x40)
            with self.assertRaises(ValueError): memory.release(address+4096)
            memory.release(address)
            with self.assertRaises(ValueError): memory.release(address)
        self.assertIn(('allocate',8192,0x3000,0x04),api.calls)
        self.assertIn(('release',address,0,0x8000),api.calls)
        with win.Memory(123,writable=False,api=api) as memory:
            with self.assertRaises(io.UnsupportedOperation): memory.allocate(4096)

    def test_win64_memory_information_layout(self):
        self.assertEqual(C.sizeof(win.MEMORY_BASIC_INFORMATION), 48)
        self.assertEqual(win.MEMORY_BASIC_INFORMATION.RegionSize.offset, 24)

    def test_read_at_does_not_move_cursor_and_partial_read_fails(self):
        api = FakeKernel()
        with win.Memory(123, api=api) as memory:
            memory.seek(0x10000)
            self.assertEqual(memory.read_at(0x20000, 4), b"\0"*4)
            self.assertEqual(memory.tell(), 0x10000)
            api.partial_read = True
            with self.assertRaisesRegex(OSError, "partial process read"):
                memory.read(4)
        self.assertIn(("close", 0x123456789), api.calls)

    def test_code_write_restores_each_regions_original_protection(self):
        api = FakeKernel()
        # Use valid canonical addresses and span two regions with different protections.
        api.protection = {0x10000: 0x20, 0x11000: 0x02}
        with win.Memory(123, api=api) as memory:
            memory.seek(0x10ffe)
            self.assertEqual(memory.write(b"ABCD"), 4)
            self.assertEqual(memory.read_at(0x10ffe, 4), b"ABCD")
        self.assertEqual(api.protection, {0x10000: 0x20, 0x11000: 0x02})
        self.assertEqual([call for call in api.calls if call[0] == "protect"], [
            ("protect", 0x10ffe, 2, 0x40), ("protect", 0x11000, 2, 0x40),
            ("protect", 0x11000, 2, 0x02), ("protect", 0x10ffe, 2, 0x20)])
        self.assertIn(("flush", 0x10ffe, 4), api.calls)

    def test_write_failure_still_restores_protection(self):
        api = FakeKernel()
        api.protection = {0x10000: 0x20}
        api.fail_write = True
        with win.Memory(123, api=api) as memory:
            memory.seek(0x10000)
            with self.assertRaises(OSError): memory.write(b"A")
        self.assertEqual(api.protection[0x10000], 0x20)

    def test_read_only_and_guarded_memory_reject_writes(self):
        api = FakeKernel()
        with win.Memory(123, writable=False, api=api) as memory:
            memory.seek(0x10000)
            with self.assertRaises(io.UnsupportedOperation): memory.write(b"A")
        api.protection[0x10000] = 0x104
        with win.Memory(123, api=api) as memory:
            memory.seek(0x10000)
            with self.assertRaisesRegex(OSError, "inaccessible write"): memory.write(b"A")

    @unittest.skipIf(os.name == "nt", "Linux backend regression test")
    def test_linux_backend_reads_and_writes_current_process(self):
        buffer = C.create_string_buffer(b"original")
        address = C.addressof(buffer)
        with process_memory.open_memory(os.getpid()) as memory:
            self.assertEqual(process_memory.read_at(memory, address, 8), b"original")
            memory.seek(address)
            memory.write(b"modified")
        self.assertEqual(buffer.value, b"modified")
        self.assertTrue(process_memory.alive(os.getpid()))
        self.assertTrue(any(start <= address < end for start, end in process_memory.writable_regions(os.getpid())))


class SparseMemory:
    def __init__(self):
        self.values = {}
        self.writes = []

    def read(self, address, size):
        return bytes(self.values.get(address+i, 0) for i in range(size))

    def write(self, address, data):
        self.writes.append((address, data))
        self.values.update((address+i, value) for i, value in enumerate(data))


class MissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        (self.data/'logs').mkdir()
        attributes = ('playMode', 'questId', 'desiredDifficulty', 'startPoint')
        values = (1, 42, 2, 7)
        (self.data/'logs/blaze.log').write_text('SCNA = '+', '.join(f'"{name}": générique("{value}")' for name, value in zip(attributes, values))+'\n', encoding='utf-8')
        (self.data/'quest_points_123.json').write_text(json.dumps({'composants':[0x40000], 'quetes':{'42':{'chaine':0x50000,'point':'Start','nom':'Test'}}}))
        self.memory = SparseMemory()
        self.memory.write(STARTING_INFO_POINTER, struct.pack('<Q',0x20000))
        self.memory.write(0x30000+0x390, struct.pack('<Q',0x60000))
        self.memory.write(0x60000, b'levels/Boot/Boot\0')
        self.memory.writes.clear()
        self.hooks = MissionHooks(self.memory,123,self.data)
        self.registers = dict(rdx=5,rcx=0x30000,rsi=0x70000,rsp=0x80000,rip=0x140001000)

    def test_mission_launch_and_startup_recreate_gdb_behavior(self):
        self.hooks.connect(self.registers)
        expected = struct.pack('<IIII',1,42,2,7)
        self.assertEqual(self.memory.read(0x20000,16), expected)
        self.assertEqual(self.memory.read(0x60000,16), b'levels/Root/Root')
        self.assertEqual(self.memory.read(0x40030,16),struct.pack('<QQ',0x50000,0x50000))
        self.hooks.startup({**self.registers,'rdx':8})
        self.assertEqual(self.memory.read(0x30018,16),expected)
        self.assertEqual(self.memory.read(0x30031,1),b'\x01')
        self.assertIn('état de démarrage 8', (self.data/'active_mission_123.txt').read_text(encoding='utf-8'))

    def test_join_and_shared_hub_clear_previous_mission_without_writes(self):
        self.hooks.launched = (1,42,2,7)
        self.hooks.connect({**self.registers,'rdx':15})
        self.assertIsNone(self.hooks.launched)
        self.assertEqual(self.memory.writes,[])
        self.hooks.launched = (1,42,2,7)
        with patch('mission_hooks.hub_game.is_hub_game', return_value=True):
            self.hooks.connect(self.registers)
        self.assertIsNone(self.hooks.launched)
        self.assertEqual(self.memory.writes,[])

    def test_missing_attributes_do_not_replay_old_mission(self):
        (self.data/'logs/blaze.log').write_text('SCNA = "questId": générique("42")\n',encoding='utf-8')
        self.hooks.launched = (1,42,2,7)
        self.hooks.connect(self.registers)
        self.assertIsNone(self.hooks.launched)
        self.assertEqual(self.memory.read(0x20000,16),b'\0'*16)

    def test_watchpoint_and_gate_restore_mission(self):
        self.hooks.launched = (1,42,2,7)
        self.hooks.playmode_write(self.registers)
        self.assertEqual(self.hooks.starting_info()[1], (1,42,2,7))
        self.memory.write(0x20000,b'\0'*16)
        self.hooks.gate(self.registers)
        self.assertEqual(self.hooks.starting_info()[1], (1,42,2,7))


class DebuggerTests(unittest.TestCase):
    def test_win64_event_and_context_abi(self):
        self.assertEqual(C.sizeof(debugger.ExceptionRecord),152)
        self.assertEqual(C.sizeof(debugger.Event),176)
        context = debugger.Context()
        self.assertEqual(context.address % 16,0)
        self.assertEqual(struct.unpack_from('<I',context.buffer,48)[0],0x100013)

    def test_hardware_breakpoints_watchpoint_and_resume_flag(self):
        context = debugger.Context()
        debugger.configure(context,[0x10000,0x20000,0x30000,0x40000])
        self.assertEqual(context.get('dr7'),0xd0000055)
        self.assertEqual(context.get('dr3'),0x40000)
        context.set('dr6',15)
        context.resume()
        self.assertEqual(context.get('dr6'),0)
        self.assertEqual(struct.unpack_from('<I',context.buffer,68)[0]&0x10000,0x10000)
        with self.assertRaisesRegex(OSError,'already has hardware breakpoints'):
            debugger.configure(context,[1,2,3,4])

    def test_unknown_game_base_is_rejected_before_attach(self):
        with patch.object(debugger,'os',SimpleNamespace(name="nt")), patch.object(debugger,'modules',return_value=iter([(0x150000000,'Anthem.exe')])):
            with self.assertRaisesRegex(OSError,'Unsupported game image'):
                debugger.run(123,threading.Event())


class DebuggerLifecycleTests(unittest.TestCase):
    def exercise(self, fail_callback=False, tls_diagnostics=False, ca_registration=False):
        stop = threading.Event()
        k = Mock()
        hooks = Mock()
        hooks.starting_info.return_value = (0x20000, (0,0,0,0))
        hooks.addresses=(0x147d8fe90,0x90080)
        hooks.completed=False
        hooks.report={}
        if fail_callback:
            (hooks.handle if ca_registration else hooks.failure if tls_diagnostics else hooks.connect).side_effect = OSError('hook read failed')
        contexts = {}
        dispositions = []
        events = [(3,None), (1,debugger.BREAKPOINT), (1,0xc0000005), (1,debugger.SINGLE_STEP)]
        def wait(event_pointer, timeout):
            event = event_pointer._obj
            event.code, code = events.pop(0)
            event.pid, event.tid = 123, 456
            if event.code == 3:
                event.info.process.thread = 10
                event.info.process.process = 11
                event.info.process.file = 12
            else:
                event.info.exception.record.code = code
                if code == debugger.SINGLE_STEP:
                    contexts['dr6'] = 1
            return 1
        def get_context(handle, address):
            for name,value in contexts.items():
                struct.pack_into('<Q',(C.c_ubyte*1232).from_address(address),debugger.Context.OFFSETS[name],value)
            return 1
        def set_context(handle,address):
            buffer = (C.c_ubyte*1232).from_address(address)
            contexts.update((name,struct.unpack_from('<Q',buffer,offset)[0]) for name,offset in debugger.Context.OFFSETS.items())
            return 1
        def continue_event(pid,tid,disposition):
            dispositions.append(disposition)
            if not events: stop.set()
            return 1
        def exit_code(handle,status):
            status._obj.value = 259
            return 1
        def duplicate(owner, original, target, output, access, inherit, flags):
            output._obj.value = original + 10
            return 1
        k.DuplicateHandle.side_effect = duplicate
        k.GetCurrentProcess.return_value = 42
        k.WaitForDebugEvent.side_effect = wait
        k.GetThreadContext.side_effect = get_context
        k.SetThreadContext.side_effect = set_context
        k.ContinueDebugEvent.side_effect = continue_event
        k.GetExitCodeThread.side_effect = exit_code
        k.SuspendThread.return_value = 0
        k.ResumeThread.return_value = 1
        with patch.object(debugger,'os',SimpleNamespace(name='nt')), \
             patch.object(debugger,'modules',return_value=iter([(0x140000000,'Anthem.exe')])), \
             patch.object(debugger,'open_memory',return_value=io.BytesIO()), \
             patch.object(debugger,'MissionHooks',return_value=hooks), \
             patch.object(debugger,'TlsFailureHooks',return_value=hooks), \
             patch('windows_ca_loader.LocalCaHooks',return_value=hooks), \
             patch.object(debugger,'debug_api',return_value=k):
            if fail_callback:
                with self.assertRaisesRegex(OSError,'hook read failed'):
                    debugger.run(123,stop,tls_diagnostics=tls_diagnostics,
                                 ca_certificate='test.crt' if ca_registration else None)
            else:
                debugger.run(123,stop,tls_diagnostics=tls_diagnostics,
                             ca_certificate='test.crt' if ca_registration else None)
        self.assertEqual(dispositions,[debugger.CONTINUE,debugger.CONTINUE,debugger.NOT_HANDLED,debugger.CONTINUE])
        if ca_registration:
            hooks.handle.assert_called_once()
            hooks.prepare.assert_called_once()
            hooks.mark_ready.assert_called_once()
            hooks.close.assert_called_once_with(release_unused=True,process_exited=False)
            hooks.starting_info.assert_not_called()
        elif tls_diagnostics:
            hooks.failure.assert_called_once()
            hooks.mark_ready.assert_called_once()
            hooks.starting_info.assert_not_called()
            hooks.validate.assert_called_once_with('Anthem.exe')
        else:
            hooks.connect.assert_called_once()
        k.DebugSetProcessKillOnExit.assert_called_once_with(False)
        k.DebugActiveProcessStop.assert_called_once_with(123)
        k.ResumeThread.assert_called_once_with(20)
        self.assertEqual(contexts['dr7'],0)
        self.assertEqual(contexts['dr0'],0)
        self.assertIn(((20,),), [tuple(call)[:1] for call in k.CloseHandle.call_args_list])
        for borrowed in (10, 11):
            self.assertNotIn(((borrowed,),), [tuple(call)[:1] for call in k.CloseHandle.call_args_list])

    def test_detach_restores_hardware_registers_and_passes_foreign_exceptions(self):
        self.exercise()

    def test_hook_failure_still_continues_event_and_restores_threads(self):
        self.exercise(fail_callback=True)

    def test_tls_mode_attaches_without_mission_state_and_restores_threads(self):
        self.exercise(tls_diagnostics=True)

    def test_tls_capture_failure_still_restores_threads(self):
        self.exercise(fail_callback=True,tls_diagnostics=True)

    def test_ca_mode_uses_connect_and_completion_breakpoints_then_detaches(self):
        self.exercise(ca_registration=True)

    def test_ca_callback_failure_still_restores_registers_and_detaches(self):
        self.exercise(fail_callback=True,ca_registration=True)


class CombatSelectionTests(unittest.TestCase):
    def test_existing_french_selection_enables_every_current_combat_patch(self):
        import auto_patches
        selection = Path(__file__).resolve().parents[2]/'data/combat_patches.txt'
        with patch.object(auto_patches,'COMBAT_CHOICE',str(selection)):
            selected = auto_patches.combat_choice()
        self.assertEqual(set(selected),{name for name,_ in auto_patches.COMBAT_PATCHES}|{'suivi'})

    def test_unknown_selection_is_reported_instead_of_silently_ignored(self):
        import auto_patches
        with tempfile.TemporaryDirectory() as directory:
            selection = Path(directory)/'patches.txt'
            selection.write_text('unknown.py\n')
            with patch.object(auto_patches,'COMBAT_CHOICE',str(selection)):
                with self.assertRaisesRegex(ValueError,'Unknown combat patches'):
                    auto_patches.combat_choice()


class InspectorTests(unittest.TestCase):
    def test_pe_report_checks_real_file_bytes_without_executing_it(self):
        from windows.inspect_game import inspect
        image = bytearray(0x1200)
        image[:2] = b'MZ'
        struct.pack_into('<I',image,0x3c,0x80)
        image[0x80:0x84] = b'PE\0\0'
        struct.pack_into('<HH',image,0x84,0x8664,1)
        struct.pack_into('<H',image,0x94,0xf0)
        optional = 0x98
        struct.pack_into('<H',image,optional,0x20b)
        struct.pack_into('<Q',image,optional+24,0x140000000)
        struct.pack_into('<I',image,optional+56,0xb000000)
        struct.pack_into('<H',image,optional+70,0x40)
        section = optional+0xf0
        image[section:section+8]=b'.text\0\0\0'
        struct.pack_into('<IIII',image,section+8,0x1000,0xa60f000,0x1000,0x200)
        image[0x9cd]=3  # RVA A60F7CD -> raw 0x200 + 0x7cd
        image[0xa4b]=15
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'Anthem.exe'
            file.write_bytes(image)
            report=inspect(file)
            self.assertEqual(report['image_base'],'0x140000000')
            self.assertTrue(report['dynamic_base'])
            self.assertTrue(report['patch_sites']['solo_mode']['matches_known_bytes'])
            self.assertTrue(report['patch_sites']['solo_start_state']['matches_known_bytes'])
            image[0x9cd]=99
            file.write_bytes(image)
            self.assertFalse(inspect(file)['patch_sites']['solo_mode']['matches_known_bytes'])
            file.write_bytes(b'MZ')
            with self.assertRaisesRegex(ValueError,'Truncated'):
                inspect(file)


if __name__ == '__main__':
    unittest.main()
