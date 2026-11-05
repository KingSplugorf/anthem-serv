import base64
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import windows_ca_loader as ca
from windows_debugger import Context


class Memory:
    def __init__(self):
        self.data, self.allocations, self.protections, self.released = {}, [], [], []

    def put(self, address, data):
        self.data.update((address+i, value) for i, value in enumerate(data))

    def read(self, address, size):
        return bytes(self.data.get(address+i, 0) for i in range(size))

    def write(self, address, data):
        if not 0x90000 <= address < 0x94000:
            raise AssertionError('CA loader may only write its allocated code/data, never original game code')
        self.put(address, data)

    def allocate(self, size):
        self.allocations.append(size)
        return 0x90000

    def protect_allocation(self, address, size, protection):
        self.protections.append((address, size, protection))

    def release(self, address):
        self.released.append(address)


def pem_fixture():
    # Deliberately synthetic DER: format decoding is local; cryptographic
    # certificate acceptance is the native importer's responsibility.
    return b'-----BEGIN CERTIFICATE-----\n'+base64.b64encode(b'\x30\x03\x02\x01\x01')+b'\n-----END CERTIFICATE-----\n'


class CaLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root/'Anthem.exe'; self.exe.write_bytes(b'inspected fixture')
        self.cert = self.root/'test.crt'; self.cert.write_bytes(pem_fixture())
        self.memory = Memory()
        self.memory.put(ca.CONNECT_ENTRY, ca.CONNECT_PREFIX)
        for address, value in ca.STUB_GUARDS: self.memory.put(address, value)
        code = b'guarded import code'
        self.memory.put(ca.CA_IMPORT_ENTRY, code)
        self.memory.put(ca.TLS_GLOBAL, struct.pack('<Q', 0x70000))
        self.addCleanup(patch.stopall)
        patch.object(ca, 'SUPPORTED_SHA256', hashlib.sha256(self.exe.read_bytes()).hexdigest()).start()
        patch.object(ca, 'CODE_GUARDS', ((ca.CA_IMPORT_ENTRY,len(code),hashlib.sha256(code).hexdigest()),)).start()
        patch.object(sys, 'stdout', io.StringIO()).start()
        self.hooks = ca.LocalCaHooks(self.memory, 123, self.cert, self.root)

    def ready(self):
        self.hooks.validate(self.exe)
        self.hooks.prepare()
        self.hooks.mark_ready()

    def context(self, host='spring18.gosredirector.ea.com', port=42230, secure=1):
        context = Context()
        for name,value in dict(rip=ca.CONNECT_ENTRY,r8=0x40000,rsp=0x50008,rdx=secure,rcx=0x60000).items():
            context.set(name,value)
        self.memory.put(0x40000,host.encode()+b'\0')
        self.memory.put(0x50030,struct.pack('<i',port))
        return context

    def test_unknown_build_or_changed_verification_code_never_allocates(self):
        self.exe.write_bytes(b'another build')
        with self.assertRaisesRegex(OSError,'SHA-256'): self.hooks.validate(self.exe)
        self.exe.write_bytes(b'inspected fixture')
        self.memory.put(ca.CA_IMPORT_ENTRY,b'changed')
        with self.assertRaisesRegex(OSError,'code differs'): self.hooks.validate(self.exe)
        self.assertEqual(self.memory.allocations,[])

    def test_only_public_pem_is_accepted_and_remote_code_is_rx(self):
        self.ready()
        self.assertEqual(self.memory.allocations,[0x2000])
        self.assertEqual(self.memory.protections,[(0x90000,0x1000,0x20)])
        report=json.loads(self.hooks.path.read_text())
        self.assertTrue(report['ready'])
        self.assertFalse(report['loaded'])
        self.assertNotIn('PRIVATE',json.dumps(report))
        self.cert.write_bytes(b'-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----')
        with self.assertRaisesRegex(ValueError,'private key'): ca.public_certificate(self.cert)
        self.cert.write_bytes(pem_fixture()+pem_fixture())
        with self.assertRaisesRegex(ValueError,'exactly one'): ca.public_certificate(self.cert)

    def test_unrelated_connections_and_uninitialized_tls_are_untouched(self):
        self.ready()
        for host,port,secure in [('unrelated.example',42230,1),('127.0.0.1',443,1),
                                ('spring18.gosredirector.ea.com.bad',42230,1),('localhost',42230,0)]:
            context=self.context(host,port,secure)
            self.hooks.handle(context,1)
            self.assertEqual(context.get('rip'),ca.CONNECT_ENTRY)
        self.memory.put(ca.TLS_GLOBAL,b'\0'*8)
        context=self.context()
        self.hooks.handle(context,1)
        self.assertEqual(context.get('rip'),ca.CONNECT_ENTRY)
        self.assertFalse(self.hooks.started)

    def test_success_and_native_rejection_are_reported_without_changing_validation(self):
        for result in (1,0,-111):
            hooks=ca.LocalCaHooks(self.memory,123,self.cert,self.root)
            hooks.validate(self.exe); hooks.prepare()
            context=self.context('localhost:42230',10041)
            hooks.handle(context,7)
            self.assertEqual(context.get('rip'),0x90000)
            other=self.context()
            hooks.handle(other,8)
            self.assertEqual(other.get('rip'),ca.CONNECT_ENTRY)
            self.memory.put(hooks.result_address,struct.pack('<iI',result,1))
            context.set('rip',hooks.completion_address)
            hooks.handle(context,7)
            self.assertEqual(context.get('rip'),ca.CONNECT_ENTRY)
            self.assertTrue(hooks.completed)
            self.assertEqual(hooks.report['native_result'],result)
            self.assertEqual(hooks.report['loaded'],result>0)
            hooks.close()
        self.assertEqual(self.memory.released,[])

    def test_incomplete_or_foreign_completion_is_not_success(self):
        self.ready(); context=self.context()
        self.hooks.handle(context,7)
        context.set('rip',self.hooks.completion_address)
        with self.assertRaisesRegex(OSError,'Unexpected thread'): self.hooks.handle(context,8)
        with self.assertRaisesRegex(OSError,'complete result'): self.hooks.handle(context,7)
        self.assertFalse(self.hooks.completed)

    def test_unexpected_stack_alignment_is_rejected_before_redirecting_thread(self):
        self.ready(); context=self.context()
        context.set('rsp',0x50000)
        self.memory.put(0x50028,struct.pack('<i',42230))
        with self.assertRaisesRegex(OSError,'call ABI'): self.hooks.handle(context,7)
        self.assertEqual(context.get('rip'),ca.CONNECT_ENTRY)
        self.assertFalse(self.hooks.started)

    def test_only_unused_allocations_are_released_after_safe_detach(self):
        self.ready(); self.hooks.close()
        self.assertEqual(self.memory.released,[0x90000])
        self.hooks.prepare(); self.hooks.close(release_unused=False)
        self.assertEqual(self.memory.released,[0x90000])
        self.hooks.prepare(); self.hooks.close(process_exited=True)
        self.assertEqual(self.memory.released,[0x90000])


class CaReadinessTests(unittest.TestCase):
    def test_exit_is_observed_before_reading_the_final_detach_result(self):
        import auto_patches
        state=dict(run_id='current',ready=True,completed=True,loaded=True,detached=False,native_result=1)
        debugger=Mock()
        def finish():
            state['detached']=True
            return 0
        debugger.poll.side_effect=finish
        with patch.object(auto_patches,'alive',return_value=True), \
             patch('builtins.open',side_effect=lambda *args,**kwargs: io.StringIO(json.dumps(state))), \
             patch.object(sys,'stdout',io.StringIO()):
            self.assertTrue(auto_patches.wait_for_local_ca('123',debugger,'current'))

    def test_mission_setup_requires_matching_run_success_and_detach(self):
        import auto_patches
        state=dict(run_id='current',ready=True,completed=True,loaded=True,detached=True,native_result=1)
        debugger=Mock(); debugger.poll.side_effect=[None,0]
        with patch.object(auto_patches,'alive',return_value=True), \
             patch('builtins.open',side_effect=[io.StringIO(json.dumps({**state,'run_id':'old'})),io.StringIO(json.dumps(state))]), \
             patch.object(auto_patches.time,'sleep'), patch.object(sys,'stdout',io.StringIO()):
            self.assertTrue(auto_patches.wait_for_local_ca('123',debugger,'current'))
        for changes in ({'loaded':False},{'detached':False},{'run_id':'old'},{'native_result':0}):
            debugger=Mock(); debugger.poll.return_value=0
            with patch.object(auto_patches,'alive',return_value=True), \
                 patch('builtins.open',return_value=io.StringIO(json.dumps({**state,**changes}))), \
                 patch.object(sys,'stdout',io.StringIO()):
                with self.assertRaisesRegex(RuntimeError,'did not succeed'):
                    auto_patches.wait_for_local_ca('123',debugger,'current')


class NativeStubTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform=='linux' and platform.machine()=='x86_64' and shutil.which('gcc'),
                         'Native Microsoft x64 ABI regression needs Linux x64 and GCC')
    def test_stub_calls_importer_and_preserves_connect_arguments_stack_and_fp_state(self):
        fixture=Path(__file__).with_name('fixtures')/'local_ca_stub.c'
        code,completion=ca.call_stub(0x1111222233334444,10,0x5555666677778888,
                                    importer=0x9999aaaabbbbcccc,continuation=0xddddeeeeffff0000)
        self.assertEqual(code[completion:completion+6],b'\xff\x25\0\0\0\0')
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder); program=folder/'stub-test'; binary=folder/'stub.bin'
            binary.write_bytes(code)
            subprocess.run(['gcc','-std=gnu11','-O0','-fno-omit-frame-pointer',str(fixture),'-lm','-o',str(program)],
                           check=True,capture_output=True,text=True,timeout=30)
            result=subprocess.run([str(program),str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__': unittest.main()
