import ctypes as C
import json
from pathlib import Path
import struct
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import windows_debugger as debugger
from mission_lifecycle import RepeatingMissionHooks
from mission_lease import HEADER
import test_mission_lifecycle as fixtures
RUN=fixtures.RUN


class RepeatingDebuggerTests(unittest.TestCase):
    def exercise(self, exit_process=False, story_leave=False):
        fixture=fixtures.MissionLifecycleTests('test_three_expeditions_restore_hub_and_use_new_startup_objects_and_points')
        fixture.setUp()
        # Its fixture producer is not the debugger under test; close and remove
        # its lease so the simulated native attachment owns the actual session.
        cleanup=fixture._cleanups.pop()
        self.assertIs(getattr(cleanup[0],'__self__',None),fixture.hooks)
        fixture.hooks.close(); fixture.hooks.lease_path.unlink()
        self.addCleanup(fixture.doCleanups)
        memory=fixture.memory
        class FileMemory:
            def seek(self,address): self.position=address
            def read_at(self,address,size): return memory.read(address,size)
            def write(self,data): memory.write(self.position,data)
            def __enter__(self): return self
            def __exit__(self,*_): pass
        instances=[]
        class Hooks(RepeatingMissionHooks):
            def __init__(self,space,pid,run_id,**options):
                super().__init__(space,pid,run_id,data=fixture.data,**options)
                instances.append(self)
        k=Mock(); stop=threading.Event(); contexts={20:{},21:{}}
        snapshots=[]
        events=[('create',456,10),('thread',457,11),('break',456,0),('connect',456,2),
                ('startup',456,8),('leave',456,0),('connect',456,3),('startup',456,8),
                ('async_left',456,3),('exit' if exit_process else 'stop',456,0)]
        def wait(pointer,timeout):
            kind,tid,value=events.pop(0)
            if kind=='stop':
                stop.set()
                return 0
            if kind=='async_left':
                fixture.event('left',value)
                return 0
            event=pointer._obj; event.pid=123; event.tid=tid
            if kind=='create': event.code=3; event.info.process.thread=value
            elif kind=='thread': event.code=2; event.info.thread.thread=value
            elif kind=='exit': event.code=5
            else:
                event.code=1
                event.info.exception.record.code=debugger.BREAKPOINT if kind=='break' else debugger.SINGLE_STEP
                context=contexts[20]
                context.update(rcx=0x30000,rdx=5,rsi=0x70000,rsp=0xA0000,rip=0x140001000,dr6=1)
                if kind=='connect':
                    fixture.event('requested',value)
                    memory.put(0x70050,struct.pack('<Q',value))
                    pointer=0x20000 if value==2 else 0x21000
                    memory.put(pointer,bytes(16))
                    memory.put(debugger.MissionHooks.starting_info.__globals__['STARTING_INFO_POINTER'],struct.pack('<Q',pointer))
                if kind in ('startup','leave'):
                    context.update(rcx=0x80000,rdx=value,dr6=4 if kind=='leave' and story_leave else 2)
            return 1
        def get_context(handle,address):
            buffer=(C.c_ubyte*1232).from_address(address)
            for name,offset in debugger.Context.OFFSETS.items():
                struct.pack_into('<Q',buffer,offset,contexts[handle].get(name,0))
            return 1
        def set_context(handle,address):
            buffer=(C.c_ubyte*1232).from_address(address)
            contexts[handle].update({name:struct.unpack_from('<Q',buffer,offset)[0] for name,offset in debugger.Context.OFFSETS.items()})
            return 1
        def continuation(pid,tid,disposition):
            snapshots.append((instances[0].phase,instances[0].generation,contexts[20].get('dr7'),contexts[21].get('dr7'),contexts[20].get('dr3')))
            return 1
        def duplicate(owner,original,target,output,access,inherit,flags): output._obj.value=original+10; return 1
        def exit_code(handle,status): status._obj.value=259; return 1
        k.WaitForDebugEvent.side_effect=wait
        k.GetThreadContext.side_effect=get_context
        k.SetThreadContext.side_effect=set_context
        k.ContinueDebugEvent.side_effect=continuation
        k.DuplicateHandle.side_effect=duplicate
        k.GetExitCodeThread.side_effect=exit_code
        k.SuspendThread.return_value=0; k.ResumeThread.return_value=1
        with patch.object(debugger,'os',SimpleNamespace(name='nt',environ={})), \
             patch.object(debugger,'modules',return_value=iter([(0x140000000,'Anthem.exe')])), \
             patch.object(debugger,'open_memory',return_value=FileMemory()), \
             patch.object(debugger,'RepeatingMissionHooks',Hooks), \
             patch.object(debugger,'debug_api',return_value=k), \
             patch.object(debugger.C,'get_last_error',return_value=121,create=True):
            debugger.run(123,stop,mission_session=RUN)
        self.assertIn(('starting',1,0xd0000055,0xd0000055,0x20000),snapshots)
        self.assertIn(('active',1,21,21,0),snapshots)
        self.assertIn(('returning',1,1,1,0),snapshots)
        self.assertIn(('starting',2,0xd0000055,0xd0000055,0x21000),snapshots)
        self.assertIn(('active',2,21,21,0),snapshots)
        self.assertEqual(contexts[20]['dr7'],1 if exit_process else 0)
        self.assertEqual(contexts[21]['dr7'],1 if exit_process else 0)
        self.assertEqual(instances[0].phase,'stopped')
        if not exit_process:
            k.DebugActiveProcessStop.assert_called_once_with(123)
        k.SuspendThread.assert_any_call(20); k.SuspendThread.assert_any_call(21)
        lease=fixture.data/f'mission_lease_123_{RUN}.bin'
        self.assertEqual(HEADER.unpack(lease.read_bytes())[-1],0)

    def test_two_launches_async_leave_and_graceful_detach_restore_every_thread(self):
        self.exercise()

    def test_story_leave_callback_releases_workers_and_restores_hub_before_next_launch(self):
        self.exercise(story_leave=True)

    def test_game_exit_revokes_workers_and_closes_without_reading_dead_game_memory(self):
        self.exercise(exit_process=True)

    def test_failed_thread_suspension_resumes_every_thread_already_paused(self):
        k=Mock()
        def active(handle,status): status._obj.value=259; return 1
        k.GetExitCodeThread.side_effect=active
        k.SuspendThread.side_effect=[0,0xffffffff]
        k.ResumeThread.return_value=1
        with self.assertRaises(OSError):
            with debugger.frozen_threads(k,{1:20,2:21}):
                self.fail('partial suspension must not enter mutation phase')
        k.ResumeThread.assert_called_once_with(20)


if __name__=='__main__': unittest.main()
