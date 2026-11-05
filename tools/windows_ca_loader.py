"""Experimental, build-specific registration of a public development CA.

The supplied static TLS bundle identified an importer that verifies certificates
and copies their public identity/key into the game's CA list. Runtime code guards
are mandatory. A hardware breakpoint at the inspected integer-argument TLS
connect entry redirects one existing game thread through a self-contained call
stub. No verification branch or executable file is patched.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import time

from tls_client_diagnostics import SUPPORTED_SHA256, cstring

CONNECT_ENTRY = 0x147D8FE90
CONNECT_PREFIX = bytes.fromhex('405355574883ec204c89c589d34889cf')
CA_IMPORT_ENTRY = 0x147D6ABC0
TLS_GLOBAL = 0x144A5DDC0
CODE_GUARDS = (
    (CA_IMPORT_ENTRY, 661, 'fb006cc0b4f246a8b2da6f095f72074656b29f1a88ee1a7542f967c8095ac611'),
    (0x147D6A190, 556, 'fcec5eaf5de4d5ffa5ec0b32fd28c8c92712a0f7031858ba01954c4a41e9e96b'),
    (0x147D6A880, 517, 'acc125bd5d9c57b0026c51adc502837ec5a1150d6827046f140acd68d0f98121'),
)
STUB_GUARDS = (
    (0x140826440, bytes.fromhex('e97b475407')),
    (0x140826000, bytes.fromhex('e98b415407')),
    (0x140826230, bytes.fromhex('e94b465407')),
    (0x140823370, bytes.fromhex('e9fb1e5407')),
    (0x1408225C0, bytes.fromhex('e99b1c5407')),
    (0x140835670, bytes.fromhex('e90b225607')),
)
LOCAL_HOSTS = {'spring18.gosredirector.ea.com', '127.0.0.1', 'localhost'}
LOCAL_PORTS = {42230, 10041}


def public_certificate(path):
    with Path(path).open('rb') as source:
        pem = source.read(0x10001).strip()
    if len(pem) > 0x10000 or b'PRIVATE KEY' in pem:
        raise ValueError('Expected a public certificate, never a private key')
    match = re.fullmatch(rb'-----BEGIN CERTIFICATE-----\s+([A-Za-z0-9+/=\s]+)\s+-----END CERTIFICATE-----', pem)
    if match is None:
        raise ValueError('Expected exactly one public PEM certificate in data/certs/test.crt')
    der = base64.b64decode(re.sub(rb'\s+', b'', match.group(1)), validate=True)
    if not der or der[0] != 0x30 or len(der) > 4096:
        raise ValueError('Certificate DER must fit the inspected importer\'s 4096-byte limit')
    return pem+b'\n', hashlib.sha256(der).hexdigest()


def call_stub(certificate_address, certificate_size, result_address,
              *, importer=CA_IMPORT_ENTRY, continuation=CONNECT_ENTRY):
    """Microsoft x64 call: preserve volatile GP, flags and legacy FP/SSE state.

    Entry RSP is 8 mod 16. Eight pushes use 64 bytes; subtracting 0x228 aligns
    the call stack and reserves 32-byte shadow space plus aligned FXSAVE64 data.
    The continuation has integer/pointer arguments; no floating arguments or
    nonvolatile upper AVX lanes need to be recovered. The final jump also works
    if the debugger detaches while the importer is still running.
    """
    code = bytearray.fromhex('9c50515241504151415241534881ec28020000480fae442420')
    code += b'\x48\xb9'+struct.pack('<Q', certificate_address)
    code += b'\xba'+struct.pack('<I', certificate_size)
    code += b'\x48\xb8'+struct.pack('<Q', importer)+b'\xff\xd0'
    code += b'\x49\xbb'+struct.pack('<Q', result_address)
    code += bytes.fromhex('41890341c7430401000000480fae4c24204881c428020000415b415a415941585a59589d')
    completion = len(code)
    code += b'\xff\x25\0\0\0\0'+struct.pack('<Q', continuation)
    return bytes(code), completion


class LocalCaHooks:
    def __init__(self, memory, pid, certificate, data=Path('data')):
        self.memory, self.pid, self.certificate = memory, pid, Path(certificate)
        self.path = Path(data)/f'local_ca_{pid}.json'
        self.allocation = None
        self.pending_thread = None
        self.started = self.completed = False
        self.report = dict(pid=pid, run_id=os.environ.get('ANTHEM_CA_RUN_ID'),
                           mode='experimental native public CA registration',
                           executable_sha256=SUPPORTED_SHA256, ready=False,
                           completed=False, loaded=False, stage='created', connections=[],
                           warning='Runtime gameplay remains unverified. TLS certificate and hostname verification stay enabled.')

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.report, indent=2)+'\n', encoding='utf-8')
        for attempt in range(10):
            try:
                temporary.replace(self.path)
                return
            except PermissionError:
                if attempt == 9: raise
                time.sleep(0.01)

    def log(self, stage, message):
        self.report.update(stage=stage, message=message)
        self.save()
        print(f'Local CA: {message}; report {self.path}', flush=True)

    def validate(self, executable):
        with open(executable, 'rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != SUPPORTED_SHA256:
            raise OSError('CA registration supports only the exact inspected executable SHA-256')
        if self.memory.read(CONNECT_ENTRY, len(CONNECT_PREFIX)) != CONNECT_PREFIX:
            raise OSError('Inspected TLS connect entry differs in memory; CA registration refused')
        for address, size, expected in CODE_GUARDS:
            actual = hashlib.sha256(self.memory.read(address, size)).hexdigest()
            if actual != expected:
                raise OSError(f'Inspected CA import/verification code differs at {address:#x}; registration refused')
        for address, expected in STUB_GUARDS:
            if self.memory.read(address, len(expected)) != expected:
                raise OSError(f'Inspected CA import/verification stub differs at {address:#x}; registration refused')
        self.pem, fingerprint = public_certificate(self.certificate)
        self.report.update(certificate_sha256=fingerprint, certificate_file=self.certificate.name,
                           certificate_bytes=len(self.pem), code_guards_verified=True)

    def prepare(self):
        # Certificate/result data stay RW; only the first page becomes RX.
        size = 0x1000+((16+len(self.pem)+1+0xfff)//0x1000)*0x1000
        self.allocation = self.memory.allocate(size)
        self.result_address = self.allocation+0x1000
        code, completion = call_stub(self.result_address+16, len(self.pem), self.result_address)
        self.completion_address = self.allocation+completion
        self.memory.write(self.result_address, b'\0'*16+self.pem+b'\0')
        self.memory.write(self.allocation, code)
        self.memory.protect_allocation(self.allocation, 0x1000, 0x20)
        self.addresses = (CONNECT_ENTRY, self.completion_address)

    def mark_ready(self):
        self.report['ready'] = True
        self.log('ready', 'ready; retry the local connection in the game if needed')

    def handle(self, context, thread):
        rip = context.get('rip')
        if rip == self.completion_address:
            if thread != self.pending_thread:
                raise OSError('Unexpected thread at CA import completion')
            result, complete = struct.unpack('<iI', self.memory.read(self.result_address, 8))
            if complete != 1:
                raise OSError('CA import did not publish a complete result')
            # The stub has already restored the thread's GP/FP registers, flags
            # and stack. Resume the original entry with RF suppressing its BP.
            context.set('rip', CONNECT_ENTRY)
            self.completed, self.pending_thread = True, None
            # The inspected add routine returns 0 for both duplicate and
            # allocation failure. Only a positive count confirms registration.
            self.report.update(completed=True, loaded=result > 0, native_result=result)
            self.log('loaded' if result > 0 else 'unconfirmed' if result == 0 else 'rejected',
                     f'game CA importer returned {result}: '+
                     ('certificate added' if result > 0 else
                      'no certificate added (duplicate or allocation failure); registration unconfirmed'
                      if result == 0 else 'certificate rejected'))
            return
        if rip != CONNECT_ENTRY:
            raise OSError('Unexpected CA loader breakpoint address')
        if self.started:
            return  # Other connecting threads retain their original behavior.
        host = cstring(self.memory, context.get('r8'))
        port = struct.unpack('<i', self.memory.read(context.get('rsp')+0x28, 4))[0]
        hostname, separator, embedded_port = host.partition(':')
        if separator and embedded_port.isdecimal(): port = int(embedded_port)
        secure = bool(context.get('rdx') & 0xffffffff)
        self.report['connections'] = (self.report['connections']+[dict(host=host, port=port, secure=secure)])[-16:]
        if not secure or hostname.lower().rstrip('.') not in LOCAL_HOSTS or port not in LOCAL_PORTS:
            self.save()
            return
        if not int.from_bytes(self.memory.read(TLS_GLOBAL, 8), 'little'):
            self.log('waiting', 'local connect observed before TLS global initialization; waiting for another connection')
            return
        if context.get('rsp') & 15 != 8:
            raise OSError('TLS connect stack does not match the inspected Microsoft x64 call ABI')
        self.log('calling', f'registering the public development certificate before {hostname}:{port}')
        self.pending_thread, self.started = thread, True
        context.set('rip', self.allocation)

    def close(self, *, release_unused=True, process_exited=False):
        if self.allocation is None: return
        # Once entered, retain this small allocation for the game lifetime. This
        # lets the self-contained stub finish even during an interrupted detach
        # or a failed context update, without freeing a possible return target.
        if process_exited:
            self.allocation = None
            return
        if self.started or not release_unused:
            self.report['allocation_retained_until_game_exit'] = True
            self.save()
        else:
            self.memory.release(self.allocation)
        self.allocation = None
