import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tls_client_diagnostics as diagnostics
from windows_debugger import Context, configure


class Memory:
    def __init__(self):
        self.data={}
        self.reads=[]
    def put(self,address,data):
        self.data.update((address+index,value) for index,value in enumerate(data))
    def read(self,address,size):
        self.reads.append((address,size))
        return bytes(self.data.get(address+index,0) for index in range(size))
    def write(self,*args):
        raise AssertionError('TLS diagnostics must never write game memory')


class ClientTlsTests(unittest.TestCase):
    def test_single_execution_breakpoint_does_not_enable_data_watchpoints(self):
        context=Context()
        context.set('dr3',0x20000)
        configure(context,(diagnostics.TLS_FAILURE_SITE,),watchpoint=None)
        self.assertEqual(context.get('dr7'),1)
        self.assertEqual(context.get('dr0'),diagnostics.TLS_FAILURE_SITE)
        self.assertEqual(context.get('dr3'),0)
        with self.assertRaises(ValueError):
            configure(Context(),(1,),watchpoint=3)

    def test_unsupported_executable_or_instruction_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'Anthem.exe'; exe.write_bytes(b'test build')
            hooks=diagnostics.TlsFailureHooks(Memory(),123,folder)
            with self.assertRaisesRegex(OSError,'exact previously inspected executable'):
                hooks.validate(exe)
            with patch.object(diagnostics,'SUPPORTED_SHA256',hashlib.sha256(exe.read_bytes()).hexdigest()):
                with self.assertRaisesRegex(OSError,'instruction differs'):
                    hooks.validate(exe)

    def test_validated_certificate_failure_exports_identity_and_bounded_code(self):
        memory=Memory()
        memory.put(diagnostics.TLS_FAILURE_SITE,diagnostics.TLS_FAILURE_INSTRUCTION)
        memory.put(diagnostics.TLS_FAILURE_FORMAT_ADDRESS,diagnostics.TLS_FAILURE_FORMAT)
        certificate=bytearray(0x1c4)
        certificate[0:2]=b'US'
        certificate[0x180:0x180+len(b'spring18.example')]=b'spring18.example'
        struct.pack_into('<I',certificate,0x1c0,256)
        memory.put(0x30050,certificate)
        memory.put(0x40000,b'spring18.example\0')
        registers=dict(rip=diagnostics.TLS_FAILURE_SITE,rbx=0xffffffffffffffea,r12=0x40000,rsp=0x30000)
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'Anthem.exe'; exe.write_bytes(b'test build')
            with patch.object(diagnostics,'SUPPORTED_SHA256',hashlib.sha256(exe.read_bytes()).hexdigest()):
                hooks=diagnostics.TlsFailureHooks(memory,123,folder)
                hooks.validate(exe)
                with patch.object(sys,'stdout',io.StringIO()):
                    hooks.mark_ready()
                    hooks.failure(registers)
            report=json.loads(hooks.path.read_text(encoding='utf-8'))
            event=report['failures'][0]
            self.assertTrue(report['ready'])
            self.assertEqual(event['raw_error_code'],-22)
            self.assertEqual(event['host'],'spring18.example')
            self.assertEqual(event['certificate_identity']['country'],'US')
            self.assertEqual(event['certificate_identity']['common_name'],'spring18.example')
            self.assertEqual(event['key_modulus_size'],256)
            self.assertEqual(len(bytes.fromhex(report['api_code']['bytes'])),0x6000)
            self.assertEqual(exe.read_bytes(),b'test build')
            self.assertFalse(hooks.path.with_suffix('.tmp').exists())
            with self.assertRaisesRegex(OSError,'instruction pointer'):
                hooks.failure({**registers,'rip':0x12345})

    def test_cstring_stops_at_null_without_crossing_a_page(self):
        memory=Memory()
        memory.put(0x1ff8,b'spring18.example\0')
        self.assertEqual(diagnostics.cstring(memory,0x1ff8),'spring18.example')
        self.assertEqual(memory.reads[0],(0x1ff8,8))
        self.assertEqual(memory.reads[1][0],0x2000)


class TlsReadinessTests(unittest.TestCase):
    def test_supervisor_ignores_stale_pid_report_until_this_run_is_ready(self):
        import auto_patches
        debugger=Mock(); debugger.poll.return_value=None
        stale=io.StringIO('{"ready":true,"run_id":"old"}')
        current=io.StringIO('{"ready":true,"run_id":"current"}')
        with patch.object(auto_patches,'alive',return_value=True), \
             patch('builtins.open',side_effect=[stale,current]) as opened, \
             patch.object(auto_patches.time,'sleep') as sleep:
            auto_patches.wait_for_tls_diagnostics('123',debugger,run_id='current')
        self.assertEqual(opened.call_count,2)
        sleep.assert_called_once_with(0.1)

    def test_debugger_exit_is_reported_without_waiting_for_a_log(self):
        import auto_patches
        debugger=Mock(); debugger.poll.return_value=2
        with patch.object(auto_patches,'alive',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'TLS debugger exited'):
                auto_patches.wait_for_tls_diagnostics('123',debugger)


if __name__ == '__main__': unittest.main()
