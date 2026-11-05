"""Passive certificate-error capture for the inspected Anthem Windows build.

Uses the observed logging call site and the Windows x64 register/stack layout.
No game functions are called and no certificate-validation result is changed.
The optional code excerpts are fixed, bounded API-code regions, not TLS keys or
heap/session buffers. This is not a CA-loader implementation.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import time

SUPPORTED_SHA256 = '63cbc85f17ebd5d9ede8d72cef07eff40945bf5000604b52d482812cbbcd0338'
TLS_FAILURE_SITE = 0x1429C8C05
TLS_FAILURE_INSTRUCTION = bytes.fromhex('488d15e40c3201')
TLS_FAILURE_FORMAT_ADDRESS = 0x143CE98F0
TLS_FAILURE_FORMAT = b'TLS/SSL certificate failure (%d) for %s:'
# These meanings are from a reference DirtySDK header. The raw code is retained
# because matching names in a reference library do not establish binary identity.
REFERENCE_ERRORS = {-20: 'certificate invalid', -21: 'certificate hostname mismatch',
                    -22: 'certificate not trusted'}


def cstring(memory, address, limit=256):
    if not address:
        return ''
    result = bytearray()
    while len(result) < limit:
        cursor = address+len(result)
        size = min(32, limit-len(result), 4096-(cursor & 4095))
        data = memory.read(cursor, size)
        result.extend(data.split(b'\0', 1)[0])
        if b'\0' in data:
            break
    return result.decode('utf-8', errors='replace')


class TlsFailureHooks:
    def __init__(self, memory, pid, data=Path('data')):
        self.memory, self.pid, self.data = memory, pid, Path(data)
        self.path = self.data/f'tls_client_{pid}.json'
        self.report = dict(pid=pid, mode='passive TLS certificate diagnostics',
                           run_id=os.environ.get('ANTHEM_TLS_RUN_ID'),
                           executable_sha256=SUPPORTED_SHA256, stage='created', ready=False, failures=[],
                           warning='No certificate checks are disabled. Reference error labels and code excerpts require interpretation.')

    def validate(self, executable):
        with open(executable, 'rb') as source:
            actual = hashlib.file_digest(source, 'sha256').hexdigest()
        if actual != SUPPORTED_SHA256:
            raise OSError('TLS diagnostics support only the exact previously inspected executable hash')
        if self.memory.read(TLS_FAILURE_SITE, len(TLS_FAILURE_INSTRUCTION)) != TLS_FAILURE_INSTRUCTION:
            raise OSError('TLS diagnostic instruction differs from inspected bytes; refusing to attach')
        if self.memory.read(TLS_FAILURE_FORMAT_ADDRESS, len(TLS_FAILURE_FORMAT)) != TLS_FAILURE_FORMAT:
            raise OSError('TLS diagnostic format string differs from inspected bytes; refusing to attach')
        # Bound the code export to the API region surrounding the observed
        # ProtoSSLControl call target. This captures code, never game heap data.
        try:
            self.report['api_code'] = dict(address=hex(0x140830000),
                                          bytes=self.memory.read(0x140830000, 0x6000).hex())
        except OSError as error:
            self.report['api_code_error'] = str(error)

    def save(self):
        self.data.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.report, indent=2)+'\n', encoding='utf-8')
        for attempt in range(10):
            try:
                temporary.replace(self.path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.01)

    def log(self, message):
        self.report['stage'] = message
        self.save()
        print(f'TLS diagnostics: {message}; report {self.path}', flush=True)

    def mark_ready(self):
        self.report['ready'] = True
        self.log('ready; retry the game connection to capture its certificate error')

    def failure(self, registers):
        if registers['rip'] != TLS_FAILURE_SITE:
            raise OSError('Unexpected TLS diagnostic instruction pointer')
        code = struct.unpack('<i', struct.pack('<I', registers['rbx'] & 0xffffffff))[0]
        if code not in REFERENCE_ERRORS:
            raise OSError('TLS logging arguments differ from the inspected certificate-error path')
        certificate = self.memory.read(registers['rsp']+0x50, 0x1c4)
        fields = [('country', 0x00, 32), ('state', 0x20, 32), ('city', 0x40, 32),
                  ('organization', 0x60, 32), ('unit', 0x80, 256), ('common_name', 0x180, 64)]
        identity = {name: certificate[offset:offset+size].split(b'\0',1)[0].decode('utf-8',errors='replace')
                    for name, offset, size in fields}
        event = dict(time=time.strftime('%H:%M:%S'), raw_error_code=code,
                     reference_error_label=REFERENCE_ERRORS[code],
                     host=cstring(self.memory, registers['r12']),
                     certificate_identity=identity,
                     key_modulus_size=struct.unpack_from('<I', certificate, 0x1c0)[0])
        self.report['failures'] = (self.report['failures']+[event])[-16:]
        self.log(f'certificate error captured: {code} ({REFERENCE_ERRORS[code]})')
