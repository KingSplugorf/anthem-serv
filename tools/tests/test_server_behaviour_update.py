import importlib.util
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

TOOLS = Path(__file__).resolve().parents[1]
SOURCE = TOOLS/'combat_patches/cave.s'
BLOB = SOURCE.with_suffix('.bin')
spec = importlib.util.spec_from_file_location('server_behaviour_update',TOOLS/'combat_patches/server_behaviours.py')
behavior_update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(behavior_update)


class ServerBehaviorUpdateTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('as') and shutil.which('objcopy'),'GNU assembly tools unavailable')
    def test_shipped_wrapper_matches_assembly_and_fits_its_reserved_space(self):
        with tempfile.TemporaryDirectory() as directory:
            obj, blob = Path(directory)/'cave.o', Path(directory)/'cave.bin'
            subprocess.run(['as','--64',str(SOURCE),'-o',str(obj)],check=True,capture_output=True)
            subprocess.run(['objcopy','-O','binary','-j','.text',str(obj),str(blob)],check=True,capture_output=True)
            self.assertEqual(blob.read_bytes(),BLOB.read_bytes())
        self.assertLessEqual(behavior_update.CAVE+len(behavior_update.CODE),behavior_update.CAVE_END)

    @unittest.skipUnless(sys.platform == 'linux' and platform.machine() == 'x86_64' and shutil.which('cc'),
                         'Native Microsoft-ABI fixture needs Linux x64 and a C compiler')
    def test_native_wrapper_updates_each_owned_behavior_without_requiring_cadence(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory/'behavior_patch.h').write_text('static const unsigned char patch[] = {'+
                ','.join(str(byte) for byte in BLOB.read_bytes())+'};\n')
            executable = directory/'behavior-update'
            source = TOOLS/'tests/fixtures/server_behaviour_update.c'
            built = subprocess.run(['cc','-std=gnu11','-O2','-Wall','-Wextra','-Werror',
                '-I',str(directory),str(source),'-o',str(executable)],capture_output=True,text=True)
            self.assertEqual(built.returncode,0,built.stderr)
            result = subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            if result.returncode == 77: self.skipTest(result.stderr.strip())
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('8 native behavior-update cases passed',result.stdout)

    def install_with(self, stub, cave):
        process = Mock()
        process.qword.return_value = behavior_update.ORIGINAL_TARGET
        process.read.side_effect = lambda address,size: stub if address == behavior_update.STUB else cave
        with patch.object(behavior_update,'Process',return_value=process), \
                patch.object(sys,'argv',['server_behaviours.py','100','poser']), \
                patch.object(behavior_update,'preflight') as preflight:
            with self.assertRaises(SystemExit): behavior_update.main()
            preflight.assert_not_called()
        process.write.assert_not_called()

    def test_changed_thunk_is_rejected_before_any_writes_or_heap_scan(self):
        self.install_with(b'\xcc'*5,b'\xcc'*len(behavior_update.CODE))

    def test_live_older_wrapper_requires_restart_before_any_writes(self):
        self.install_with(behavior_update.STUB_PATCHED,b'\x90'*len(behavior_update.CODE))
