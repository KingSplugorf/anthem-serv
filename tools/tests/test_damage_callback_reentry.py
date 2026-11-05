import contextlib
import io
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'combat_patches'))
import server_damage


class Memory:
    def __init__(self, obj, code):
        self.blocks = {server_damage.CALLBACK_OBJECT: struct.pack('<Q', obj),
                       server_damage.CALLBACK_FUNCTION: struct.pack('<Q', server_damage.HEALTH_CALLBACK),
                       server_damage.CODE_ADDRESS: server_damage.CODE,
                       server_damage.HEALTH_CALLBACK: code}
        self.writes = []

    def seek(self, address): self.position = address
    def read(self, size): return self.blocks[self.position][:size]
    def write(self, data): self.writes.append((self.position, data))


class CallbackReentryTests(unittest.TestCase):
    def install(self, memory):
        with patch.object(server_damage, 'open_memory', return_value=memory), \
             patch.object(sys, 'argv', ['server_damage.py', '123', 'poser']), \
             contextlib.redirect_stdout(io.StringIO()):
            server_damage.main()

    def test_repeated_expeditions_preserve_the_verified_health_callback_without_writes(self):
        for obj in (server_damage.CODE_ADDRESS, server_damage.HEALTH_CALLBACK):
            memory = Memory(obj, server_damage.HEALTH_CODE)
            for _ in range(3): self.install(memory)
            self.assertEqual(memory.writes, [])

    def test_changed_code_or_foreign_object_is_rejected_without_writes(self):
        for obj, code in ((server_damage.CODE_ADDRESS, b'changed'), (0x123456, server_damage.HEALTH_CODE)):
            memory = Memory(obj, code)
            with self.assertRaises(SystemExit): self.install(memory)
            self.assertEqual(memory.writes, [])
