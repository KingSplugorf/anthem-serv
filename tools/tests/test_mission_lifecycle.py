import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mission_hooks import STARTING_INFO_POINTER
from mission_lifecycle import RepeatingMissionHooks, TransitionPoints, EventLog, LEAVE_SUCCESS, LEAVE_CODE, PLAYER_SLOTS
from mission_lease import GuardedMemory, Lease, MissionInactive
from end_expedition import STORY_SUCCESS_CALLBACK, STORY_SUCCESS_CODE
import windows_debugger as debugger

RUN = '1234567890abcdef1234567890abcdef'


class Memory:
    def __init__(self):
        self.bytes, self.writes, self.reads = {}, [], []

    def put(self, address, value):
        self.bytes.update((address + index, byte) for index, byte in enumerate(value))

    def read(self, address, size):
        self.reads.append((address, size))
        try:
            return bytes(self.bytes[address + index] for index in range(size))
        except KeyError:
            raise OSError(f'unmapped {address:#x}')

    def write(self, address, data):
        self.read(address, len(data))
        self.writes.append((address, bytes(data)))
        self.put(address, data)


class MissionLifecycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        (self.data / 'logs').mkdir()
        self.blaze = self.data / 'logs/blaze.log'
        self.blaze.write_text('BLAZE_LOCAL_LIFECYCLE_READY = 1\n')
        self.memory, self.now = Memory(), 0
        m = self.memory
        m.put(STARTING_INFO_POINTER, struct.pack('<Q', 0x20000))
        m.put(0x20000, bytes(16))
        m.put(0x30390, struct.pack('<Q', 0x60000))
        m.put(0x60000, b'levels/Root/Root\0'.ljust(64, b'\0'))
        m.put(0x70050, struct.pack('<Q', 2))
        m.put(0x80018, bytes(16))
        m.put(0x80031, b'\0')
        m.put(LEAVE_SUCCESS, LEAVE_CODE)
        m.put(STORY_SUCCESS_CALLBACK, STORY_SUCCESS_CODE)
        m.put(PLAYER_SLOTS, bytes(24))
        m.put(0x14096B9A0, bytes(5))
        m.put(0x90000, b'Enclave_HQ\0'.ljust(64, b'\0'))
        m.put(0x91000, b'Fort_Ext\0'.ljust(64, b'\0'))
        m.put(0x92000, b'Mission_B\0'.ljust(64, b'\0'))
        self.component(0x40000)
        self.hooks = RepeatingMissionHooks(m, 123, RUN, self.data, clock=lambda: self.now)
        self.addCleanup(self.hooks.close)
        self.hooks.mark_ready()
        self.registers = {'rdx':5, 'rcx':0x30000, 'rsi':0x70000, 'rsp':0xA0000, 'rip':0x140001000}
        m.writes.clear()

    def component(self, address):
        m = self.memory
        m.put(address, struct.pack('<Q', 0x143ABC000))
        m.put(address + 0x30, struct.pack('<QQI', 0x90000, 0x90000, 1))
        table = {'schema':2, 'composants':[address],
                 'originaux':[{'component':address, 'identity':0x143ABC000,
                               'pointers':[0x90000,0x90000], 'names':['Enclave_HQ','Enclave_HQ']}],
                 'quetes':{'1401555469':{'chaine':0x91000,'point':'Fort_Ext','nom':'freeplay_quest'},
                           '680915601':{'chaine':0x92000,'point':'Mission_B','nom':'test_mission'}}}
        (self.data/'quest_points_123.json').write_text(json.dumps(table))

    def event(self, event, game=2, quest=1401555469, **changes):
        record = {'schema':1,'server_run_id':'server-one','event':event,'persona_id':1000,
                  'game_id':game,'attributes':[1,quest,1,0], **changes}
        with self.blaze.open('a', encoding='utf-8') as output:
            output.write('MISSION_EVENT = '+json.dumps(record)+'\n')

    def launch(self, game=2, quest=1401555469):
        self.event('requested',game,quest)
        self.memory.put(0x70050,struct.pack('<Q',game))
        self.hooks.connect(self.registers)
        self.assertEqual(self.hooks.phase,'starting')
        self.hooks.startup({**self.registers,'rcx':0x80000,'rdx':8})
        self.assertEqual(self.hooks.phase,'active')

    def test_three_expeditions_restore_hub_and_use_new_startup_objects_and_points(self):
        for index,(game,quest,point) in enumerate([(2,1401555469,0x91000),(3,680915601,0x92000),(4,1401555469,0x91000)],1):
            pointer=0x20000+index*0x1000
            self.memory.put(pointer,bytes(16))
            self.memory.put(STARTING_INFO_POINTER,struct.pack('<Q',pointer))
            self.launch(game,quest)
            self.assertEqual(self.hooks.generation,index)
            self.assertEqual(self.memory.read(pointer,16),struct.pack('<IIII',1,quest,1,0))
            self.assertEqual(self.memory.read(0x40030,16),struct.pack('<QQ',point,point))
            self.assertIsNone(self.hooks.launched)
            self.assertTrue(self.hooks.lease.active(index))
            self.assertEqual(debugger.mission_breakpoints(self.hooks),((debugger.MISSION_CONNECT_STATE,LEAVE_SUCCESS,STORY_SUCCESS_CALLBACK),None))
            self.memory.put(PLAYER_SLOTS,struct.pack('<QQQ',0xB0000,0xB1000,0xB2000))
            self.hooks.leave(self.registers)
            self.assertEqual(self.hooks.phase,'returning')
            self.assertFalse(self.hooks.lease.active(index))
            self.assertEqual(self.memory.read(PLAYER_SLOTS,24),bytes(24))
            self.assertEqual(self.memory.read(0x40030,16),struct.pack('<QQ',0x90000,0x90000))
            self.assertEqual(debugger.mission_breakpoints(self.hooks),((debugger.MISSION_CONNECT_STATE,),None))

    def test_cancel_mid_startup_disarms_overrides_and_late_startup_cannot_replay_it(self):
        self.event('requested')
        self.hooks.connect(self.registers)
        self.event('cancelled')
        self.hooks.poll()
        self.assertEqual(self.hooks.phase,'idle')
        self.assertEqual(self.memory.read(0x40030,16),struct.pack('<QQ',0x90000,0x90000))
        self.memory.writes.clear()
        self.hooks.startup({**self.registers,'rcx':0x80000,'rdx':8})
        self.hooks.gate(self.registers)
        self.assertEqual(self.memory.writes,[])
        self.launch(3)

    def test_late_old_leave_and_duplicate_request_cannot_cancel_new_active_mission(self):
        self.launch()
        self.hooks.leave(self.registers)
        self.launch(3,680915601)
        self.event('left',2)
        self.event('requested',3,680915601)
        self.hooks.poll()
        self.assertEqual(self.hooks.phase,'active')
        self.assertEqual(self.hooks.generation,2)
        self.assertTrue(self.hooks.lease.active(2))

    def test_mismatched_game_or_other_persona_request_never_writes_memory(self):
        self.event('requested',3)
        self.event('requested',2,persona_id=1001)
        self.hooks.connect(self.registers)
        self.assertEqual(self.memory.writes,[])
        self.assertEqual(self.hooks.phase,'queued')

    def test_old_worker_generation_is_revoked_before_next_expedition_is_active(self):
        import io
        self.launch()
        base=io.BytesIO(bytes(10))
        old=GuardedMemory(base,Lease(self.hooks.lease_path,RUN),1)
        self.addCleanup(old.close)
        old.write(b'one')
        self.hooks.leave(self.registers)
        with self.assertRaises(MissionInactive): old.write(b'old')
        self.launch(3)
        with self.assertRaises(MissionInactive): old.write(b'old')
        self.assertEqual(base.getvalue(),b'one'+bytes(7))

    def test_stale_component_is_not_overwritten_and_refreshed_metadata_allows_retry(self):
        self.memory.put(0x40000,struct.pack('<Q',0xDEADBEEF))
        self.event('requested')
        self.hooks.connect(self.registers)
        self.assertEqual(self.hooks.phase,'failed')
        self.assertEqual(self.memory.writes,[])
        self.component(0x50000)
        self.launch(3)
        self.hooks.leave(self.registers)
        self.assertEqual(self.memory.read(0x50030,16),struct.pack('<QQ',0x90000,0x90000))

    def test_return_skips_foreign_or_freed_transition_components(self):
        self.launch()
        foreign=struct.pack('<QQ',0x92000,0x92000)
        self.memory.put(0x40030,foreign)
        self.hooks.leave(self.registers)
        self.assertEqual(self.memory.read(0x40030,16),foreign)
        self.assertFalse(self.hooks.lease.active(1))

    def test_timeout_and_disconnect_release_leases_and_allow_a_fresh_request(self):
        self.event('requested')
        self.hooks.connect(self.registers)
        self.now=181
        self.hooks.poll()
        self.assertEqual(self.hooks.phase,'failed')
        self.launch(3)
        self.event('disconnected',3)
        self.hooks.poll()
        self.assertEqual(self.hooks.phase,'idle')
        self.assertFalse(self.hooks.lease.active(2))
        self.launch(4)

    def test_idle_attach_and_active_mission_never_watch_a_missing_startup_pointer(self):
        self.memory.put(STARTING_INFO_POINTER,bytes(8))
        self.assertEqual(debugger.mission_breakpoints(self.hooks),((debugger.MISSION_CONNECT_STATE,),None))
        self.memory.put(STARTING_INFO_POINTER,struct.pack('<Q',0x20000))
        self.launch()
        self.memory.put(STARTING_INFO_POINTER,bytes(8))
        self.assertEqual(debugger.mission_breakpoints(self.hooks),((debugger.MISSION_CONNECT_STATE,LEAVE_SUCCESS,STORY_SUCCESS_CALLBACK),None))

    def test_event_reader_ignores_old_log_handles_partial_lines_and_log_replacement(self):
        reader=EventLog(self.blaze)
        with self.blaze.open('ab') as output: output.write(b'MISSION_EVENT = {"event":')
        self.assertEqual(reader.poll(),[])
        with self.blaze.open('ab') as output: output.write(b'"requested"}\n')
        self.assertEqual(reader.poll(),[{'event':'requested'}])
        self.blaze.write_text('new\n')
        self.assertEqual(reader.poll(),[{'event':'server_reset'}])


if __name__=='__main__': unittest.main()
