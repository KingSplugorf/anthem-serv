import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'combat_patches/health'))
import shield_flags as fix

class Memory:
    def __init__(self):
        self.values={fix.SITE:fix.ORIGINAL,fix.GENERIC:fix.NATIVE_PROLOGUE,
                     fix.CAVE:b'\xcc'*len(fix.CODE),fix.server_health.CODE_ADDRESS:fix.server_health.CODE}
        self.writes=[]
    def read(self,address,size):return self.values[address][:size]
    def qword(self,address):return fix.server_health.CODE_ADDRESS
    def write(self,address,data):
        self.writes.append((address,data))
        old=self.values[address];self.values[address]=data+old[len(data):]

class ShieldFlagsTests(unittest.TestCase):
    def test_install_repeat_restore_and_reject_foreign_code(self):
        memory=Memory();fix.install(memory,'poser')
        self.assertEqual(fix.install(memory,'etat'),'enabled')
        fix.install(memory,'poser');self.assertEqual(len(memory.writes),2)
        fix.install(memory,'retirer');self.assertEqual(memory.values[fix.SITE],fix.ORIGINAL)
        for address in (fix.SITE,fix.GENERIC,fix.CAVE,fix.server_health.CODE_ADDRESS):
            memory=Memory();memory.values[address]=b'bad'*1000
            with self.assertRaises(ValueError):fix.install(memory,'poser')
            self.assertEqual(memory.writes,[])

    @unittest.skipUnless(shutil.which('cc') and shutil.which('as') and shutil.which('objcopy'),'Native fixture tools unavailable')
    def test_machine_code_scopes_flags_to_exact_bridge_and_preserves_other_bits(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);obj=directory/'flags.o';blob=directory/'flags.bin'
            subprocess.run(['as','--64',str(ROOT/'combat_patches/health/shield_flags.s'),'-o',str(obj)],check=True)
            subprocess.run(['objcopy','-O','binary','-j','.text',str(obj),str(blob)],check=True)
            self.assertEqual(blob.read_bytes(),fix.BLOB)
            (directory/'patch.h').write_text('static unsigned char patch[]={'+','.join(map(str,fix.CODE))+'};')
            source=ROOT/'tests/fixtures/shield_flags.c';executable=directory/'test'
            subprocess.run(['cc','-O0','-I',str(directory),str(source),'-o',str(executable)],check=True)
            result=subprocess.run([str(executable)],capture_output=True,text=True)
            if result.returncode==77:self.skipTest('Fixed-address executable mapping unavailable')
            self.assertEqual(result.returncode,0,result.stderr)
