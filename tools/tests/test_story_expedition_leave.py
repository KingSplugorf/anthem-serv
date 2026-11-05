import ctypes as C
import mmap
import os
from pathlib import Path
import platform
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import end_expedition as leave


class Memory:
    def __init__(self):
        self.data, self.writes = {}, []
        for address, original, _ in leave.patches(): self.put(address, original)
    def put(self, address, data): self.data.update((address+i, value) for i, value in enumerate(data))
    def seek(self, address): self.position = address
    def read(self, size): return bytes(self.data[self.position+i] for i in range(size))
    def write(self, data):
        self.writes.append((self.position, bytes(data)))
        self.put(self.position, data)


class StoryLeaveTests(unittest.TestCase):
    def test_upgrade_from_freeplay_only_is_idempotent_and_changes_one_story_byte(self):
        memory = Memory()
        memory.put(leave.NO_EXPEDITION_JUMP, leave.SUCCESS_ON_NO_EXPEDITION)
        leave.apply_supported(memory, leave.patches())
        self.assertEqual(memory.writes, [(leave.STORY_EMPTY_BRANCH+1, b'\x34')])
        leave.apply_supported(memory, leave.patches())
        self.assertEqual(len(memory.writes), 1)
        leave.apply_supported(memory, leave.removal())
        memory.seek(leave.STORY_LEAVE_ENTRY)
        self.assertEqual(memory.read(len(leave.STORY_LEAVE_CODE)), leave.STORY_LEAVE_CODE)

    def test_unknown_story_method_prevents_all_writes_including_freeplay_patch(self):
        memory = Memory()
        memory.put(leave.STORY_LEAVE_ENTRY, b'\x90')
        with self.assertRaises(SystemExit): leave.apply_supported(memory, leave.patches())
        self.assertEqual(memory.writes, [])

    @unittest.skipUnless(os.name == 'posix' and platform.machine().lower() in ('x86_64', 'amd64'), 'native x64 fixture')
    def test_native_method_acknowledges_empty_list_and_preserves_nonempty_behavior(self):
        # SysV-to-Windows-ABI bridge; the exported leaf method has only internal
        # relative jumps. The callback writes Success's DL to its own object.
        bridge = bytes.fromhex('4889f84989c94889f14531c04883ec28ffd04883c428c3')
        with mmap.mmap(-1, 4096, prot=mmap.PROT_READ|mmap.PROT_WRITE|mmap.PROT_EXEC) as code:
            base = C.addressof(C.c_char.from_buffer(code))
            code[:len(bridge)] = bridge
            code[128:131] = bytes.fromhex('8811c3')
            call = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_uint64, C.c_void_p)(base)
            for body, empty_success in ((leave.STORY_LEAVE_CODE, 0), (leave.STORY_LEAVE_PATCHED, 1)):
                code[256:256+len(body)] = body
                for players, expected in (([], empty_success), ([52], 1), ([99], 0), ([52,99], 0)):
                    vector = C.create_string_buffer(4 + max(1,len(players))*0x58)
                    struct.pack_into('<I', vector, 0, len(players))
                    for index, player in enumerate(players): struct.pack_into('<Q', vector, 4+index*0x58, player)
                    executor, callback = C.create_string_buffer(0x128), C.create_string_buffer(32)
                    struct.pack_into('<Q', executor, 0x120, C.addressof(vector)+4)
                    struct.pack_into('<Q', callback, 0x18, base+128)
                    call(base+256, C.addressof(executor), 52, C.addressof(callback))
                    self.assertEqual(callback.raw[0], expected, (players, empty_success))
