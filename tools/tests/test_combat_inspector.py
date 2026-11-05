import importlib.util
from pathlib import Path
import struct
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('combat_inspector', Path(__file__).resolve().parents[1]/'windows/inspect_combat.py')
inspector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)


class CombatInspectorTests(unittest.TestCase):
    def test_invalid_or_over_budget_reads_never_reach_the_process_reader(self):
        reader = Mock()
        view = inspector.View(reader)
        for address, size in ((0,8), (0x10000,8193), ((1<<47)-4,8)):
            with self.assertRaises(OSError): view.read(address,size)
        view.calls = 20000
        with self.assertRaises(OSError): view.read(0x10000,8)
        reader.assert_not_called()

    def fixture(self):
        memory = {}
        def put(address, data): memory.update((address+i,byte) for i,byte in enumerate(data))
        def read(address,size):
            try: return bytes(memory[address+i] for i in range(size))
            except KeyError: raise OSError('unmapped fixture memory')
        for address in inspector.NATIVE.values(): put(address, bytes(4096))
        put(0x1446CECA0, bytes(8))
        put(inspector.RECORDS_POINTER, bytes(8))
        put(0x142B2ABE0, struct.pack('<3Q',0,0,0x100000))
        put(0x1537377D0, bytes(12)); put(0x1537376E0, bytes(16))
        put(0x1537377E8, struct.pack('<f',1))
        put(0x100000, struct.pack('<Q',inspector.SERVER_CHARACTER))
        put(0x1002B8, struct.pack('<Q',0x200000))
        put(0x1001B0, struct.pack('<3f',1,2,3))
        put(0x100058, struct.pack('<Q',0x300000))
        put(0x100320, struct.pack('<Q',0x400000))
        put(0x400020, struct.pack('<Q',0x500000))
        put(0x500000,b'EXLStorm_Template\0'.ljust(96,b'\0'))
        put(0x300008, bytes(3)); put(0x300040,struct.pack('<3f',1,2,3))
        return read, put, memory

    def test_missing_optional_state_keeps_code_export_and_player_fallback_without_writes(self):
        read, _, memory = self.fixture()
        before = dict(memory)
        report = inspector.collect(read)
        self.assertEqual(report['actors'][0]['name'], 'EXLStorm_Template')
        self.assertEqual(report['actors'][0]['server_player'], 0x200000)
        self.assertEqual(report['player_damage_bonus'],1)
        self.assertEqual(len(report['code']),len(inspector.NATIVE))
        self.assertIn('error',report['server_time'])
        self.assertEqual(memory,before)

    def test_jump_cycles_are_bounded_and_do_not_repeat_exports(self):
        read, put, _ = self.fixture()
        first,second=list(inspector.NATIVE.values())[:2]
        put(first,b'\xe9'+struct.pack('<i',second-first-5))
        put(second,b'\xe9'+struct.pack('<i',first-second-5))
        report=inspector.collect(read)
        self.assertEqual(report['code'][str(first)]['jump_target'],second)
        self.assertEqual(report['code'][str(second)]['jump_target'],first)
        self.assertEqual(len(report['code']),len(inspector.NATIVE))

    def test_actor_reuse_is_reported_without_parsing_foreign_component_data(self):
        read,put,_=self.fixture()
        put(0x100000,struct.pack('<Q',0x145000000))
        report=inspector.collect(read)
        self.assertIn('not a live',report['actors'][0]['error'])
        self.assertNotIn('components',report['actors'][0])

    def test_client_server_position_gap_uses_only_an_owned_client_health_component(self):
        read,put,_=self.fixture()
        health,block,entity=0x600000,0x700000,0x800000
        put(0x142B2ABE8,struct.pack('<Q',health))
        put(health,struct.pack('<QQ',0x143A8CEC0,block))
        put(block,struct.pack('<Q',entity))
        put(block+8,bytes([1,4,0]))
        put(block+0x20,struct.pack('<Q',health))
        put(block+0xC0,struct.pack('<3f',4,6,3))
        put(entity,struct.pack('<Q',0x143A6FF30))
        put(entity+0x58,struct.pack('<Q',block))
        report=inspector.collect(read)
        self.assertEqual(report['client_player']['entity'],entity)
        self.assertEqual(report['client_server_position_gap'],5)
        put(block+0x20,bytes(8))
        report=inspector.collect(read)
        self.assertIn('not owned',report['client_player']['error'])
        self.assertNotIn('client_server_position_gap',report)

    def test_gear_probe_is_bounded_and_records_independent_behavior_slots(self):
        read,put,_=self.fixture()
        component,slots,entity=0x600000,0x700000,0x800000
        put(component+0x38,struct.pack('<QQ',slots,slots+0x88))
        put(component+0x774,struct.pack('<I',1))
        put(slots,struct.pack('<I',1)); put(slots+0x20,struct.pack('<Q',entity))
        put(entity,struct.pack('<Q',inspector.ACTION_GEAR))
        put(entity+0x2B8,bytes(32))
        report=inspector.gear(inspector.View(read),component)
        self.assertEqual(len(report['slots'][0]['behaviors']),4)
        put(component+0x40,struct.pack('<Q',slots+0x88*65))
        with self.assertRaises(OSError): inspector.gear(inspector.View(read),component)

    def player_health_fixture(self):
        read,put,memory = self.fixture()
        health,block = 0x600000,0x300000
        put(block,struct.pack('<Q',0x100000))
        put(block+8,bytes([1,4,0]))
        put(block+0x20,struct.pack('<Q',health))
        put(health,struct.pack('<QQ',inspector.PLAYER_HEALTH,block))
        put(health+0x20,struct.pack('<ff',40,100))
        put(health+0x128,struct.pack('<f',0.4))
        put(health+0x158,bytes([0]))
        put(health+0x160,bytes(8))
        return read,put,memory,health

    def test_health_recording_observes_damage_and_healing_without_altering_health(self):
        read,put,memory,health = self.player_health_fixture()
        elapsed = [0.0]
        def pause(seconds):
            elapsed[0] += seconds
            put(health+0x20,struct.pack('<f',65 if elapsed[0] >= 0.5 else 20))
        samples = inspector.watch_health(inspector.View(read),0.5,
                                        clock=lambda:elapsed[0],pause=pause)
        self.assertEqual([sample['elapsed'] for sample in samples],[0,0.25,0.5])
        self.assertEqual([sample['server']['components'][0]['health'] for sample in samples],[40,20,65])
        before = dict(memory)
        inspector.player_health(inspector.View(read))
        self.assertEqual(memory,before)

    def test_health_recording_revalidates_component_ownership_each_sample(self):
        read,put,_,health = self.player_health_fixture()
        before = inspector.player_health(inspector.View(read))
        self.assertEqual(before['server']['components'][0]['health'],40)
        put(health+8,struct.pack('<Q',0x700000))
        after = inspector.player_health(inspector.View(read))
        self.assertEqual(after['server']['components'],[])

    def test_health_recording_duration_is_bounded_before_any_process_read(self):
        reader = Mock()
        for duration in (-1,31,float('nan'),float('inf')):
            with self.assertRaises(ValueError): inspector.watch_health(inspector.View(reader),duration)
        reader.assert_not_called()
