import contextlib
import io
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'combat_patches/ai'))
import target
import perception


class Process:
    def __init__(self, successor=True):
        self.memory = self
        self.data, self.writes = {}, []
        self.put(target.CODE_ADDRESS, target.CODE)
        self.put(target.HAS_ENEMIES_CODE, target.HAS_ENEMIES_BYTES)
        self.put(target.player_list.CODE_ADDRESS, target.player_list.CODE)
        self.put(perception.CODE_ADDRESS, perception.CODE)
        # The installed perception fallback-pointer table is mutable by design.
        self.put(perception.PREVIOUS, bytes([0x11])*(8*len(perception.SLOTS)))
        successors = {name: perception.entry(index) for index, (name, _) in enumerate(perception.SLOTS)}
        for name, slot, replacement, original in target.SLOTS:
            value = successors.get(name, replacement) if successor else original
            self.put(slot, struct.pack('<Q', value))
        self.put(target.player.PLAYER_SLOT, struct.pack('<Q', 0x12345000))
    def put(self, address, data): self.data.update((address+i, byte) for i, byte in enumerate(data))
    def read(self, address, size): return bytes(self.data[address+i] for i in range(size))
    def qword(self, address): return struct.unpack('<Q', self.read(address, 8))[0]
    def seek(self, address): self.position = address
    def write(self, data):
        self.writes.append((self.position, bytes(data)))
        self.put(self.position, data)


class TargetReentryTests(unittest.TestCase):
    def install(self, process, player='0'):
        with patch.object(target, 'Process', return_value=process), \
             patch.object(sys, 'argv', ['target.py', '123', 'poser', player]), \
             contextlib.redirect_stdout(io.StringIO()):
            target.main()

    def test_repeated_bootstrap_preserves_perception_and_the_current_player(self):
        process = Process()
        for _ in range(3): self.install(process)
        self.assertEqual(process.writes, [])
        self.assertEqual(process.qword(target.player.PLAYER_SLOT), 0x12345000)

    def test_unknown_entry_is_rejected_before_any_pointer_or_code_write(self):
        process = Process()
        process.put(target.SLOTS[0][1], struct.pack('<Q', 0x149123456))
        with self.assertRaises(SystemExit): self.install(process)
        self.assertEqual(process.writes, [])
        self.assertEqual(process.qword(target.player.PLAYER_SLOT), 0x12345000)

    def test_changed_successor_code_is_not_accepted(self):
        process = Process()
        process.put(perception.CODE_ADDRESS+0x100, b'\x90')
        with self.assertRaises(SystemExit): self.install(process)
        self.assertEqual(process.writes, [])

    def test_first_bootstrap_still_installs_target_entries_and_explicit_player(self):
        process = Process(successor=False)
        self.install(process, '0x12346000')
        self.assertEqual(process.qword(target.player.PLAYER_SLOT), 0x12346000)
        for _, slot, replacement, _ in target.SLOTS:
            self.assertEqual(process.qword(slot), replacement)
