import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('inspect_expedition', Path(__file__).resolve().parents[1]/'windows/inspect_expedition.py')
inspector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)


class ExpeditionInspectorTests(unittest.TestCase):
    def fixture(self):
        memory = {}
        def put(address, data):
            memory.update((address+i, value) for i, value in enumerate(data))
        def read(address, size):
            try: return bytes(memory[address+i] for i in range(size))
            except KeyError: raise OSError('unmapped fixture memory')
        table, stub, target = 0x143A49838, 0x140100000, 0x148100000
        put(inspector.MANAGER, struct.pack('<Q', 0x10000))
        put(0x10048, struct.pack('<Q', 0x20000))
        put(0x20000, struct.pack('<Q', table))
        put(0x20100, bytes(16))
        put(table, struct.pack('<32Q', stub, *([0]*31)))
        put(inspector.FREEPLAY_TABLE, bytes(32*8))
        put(stub, (b'\xe9'+struct.pack('<i', target-stub-5)).ljust(768, b'\x90'))
        put(target, b'\xc3'.ljust(768, b'\x90'))
        put(inspector.FREEPLAY_LEAVE, bytes(768))
        return read, put, memory, table, stub, target

    def test_read_only_export_records_executor_and_follows_bounded_jump(self):
        read, _, memory, table, stub, target = self.fixture()
        before = dict(memory)
        report = inspector.collect(read)
        self.assertEqual(report['executor_table'], table)
        self.assertEqual(report['expeditions']['count'], 0)
        self.assertEqual(report['code'][str(stub)]['jump_target'], target)
        self.assertEqual(len(report['code'][str(target)]['bytes']), 768*2)
        self.assertEqual(memory, before)

    def test_unmapped_method_is_reported_without_aborting_other_export(self):
        read, put, _, table, _, _ = self.fixture()
        put(table, struct.pack('<Q', 0x140200000))
        report = inspector.collect(read)
        self.assertIn('error', report['code'][str(0x140200000)])
        self.assertIn(str(inspector.FREEPLAY_LEAVE), report['code'])
