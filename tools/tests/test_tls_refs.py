import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from windows.inspect_tls_refs import IMAGE_BASE, context, inspect, lea_references, wrapper_pairs


class TlsReferenceTests(unittest.TestCase):
    def section(self):
        return dict(rva=0x1000,raw_offset=0x200,raw_size=0x600)

    def test_rip_references_include_signed_displacements_and_keep_counts(self):
        data=bytearray(0x800)
        target=IMAGE_BASE+0x1010
        for index in range(10):
            offset=0x300+index*16
            rva=0x1000+offset-0x200
            data[offset:offset+7]=b'\x48\x8d\x0d'+struct.pack('<i',target-(IMAGE_BASE+rva+7))
        references,counts=lea_references(data,[self.section()],{target:'TLS string'})
        self.assertEqual(counts[target],10)
        self.assertEqual(len(references[target]),6)
        self.assertEqual(references[target][0],0x1100)

    def test_wrapper_candidates_must_share_a_real_target(self):
        data=bytearray(0x800)
        for offset,prefix in [(0x220,b'\x41\xb0\x01\xe9'),(0x230,b'\x45\x33\xc0\xe9')]:
            rva=0x1000+offset-0x200
            data[offset:offset+8]=prefix+struct.pack('<i',0x1200-(rva+8))
        result=wrapper_pairs(data,[self.section()])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['target_rva'],0x1200)
        data[0x234:0x238]=struct.pack('<i',0x1210-(0x1030+8))
        self.assertEqual(wrapper_pairs(data,[self.section()]),[])

    def test_context_respects_function_boundaries(self):
        data=bytes(range(256))*8
        result=context(data,[self.section()],[(0x1000,0x1030)],[0x1000],0x1020)
        self.assertEqual(result['function_start'],hex(IMAGE_BASE+0x1000))
        self.assertEqual(len(bytes.fromhex(result['excerpts'][0]['bytes'])),48)
        self.assertEqual(len(bytes.fromhex(result['excerpts'][1]['bytes'])),16)

    def test_complete_report_preserves_file_and_checks_hash_and_string_bytes(self):
        data=bytearray(0x1200)
        data[:2]=b'MZ'; struct.pack_into('<I',data,0x3c,0x80)
        data[0x80:0x84]=b'PE\0\0'; struct.pack_into('<HH',data,0x84,0x8664,1)
        struct.pack_into('<H',data,0x94,0xf0)
        struct.pack_into('<H',data,0x98,0x20b)
        struct.pack_into('<Q',data,0x98+24,IMAGE_BASE)
        section=0x98+0xf0
        struct.pack_into('<IIII',data,section+8,0x1000,0x1000,0x1000,0x200)
        struct.pack_into('<I',data,section+36,0x60000020)
        text='ProtoSSL Global State'
        data[0x500:0x500+len(text)]=text.encode()
        data[0x220:0x227]=b'\x48\x8d\x0d'+struct.pack('<i',0x1300-(0x1020+7))
        layout=dict(sha256=hashlib.sha256(data).hexdigest(),tls_diagnostics=dict(samples=[dict(text=text,file_offset='0x500',encoding='ascii')]))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'Anthem.exe'; path.write_bytes(data)
            report=inspect(path,layout)
            self.assertEqual(report['string_references'][0]['reference_count'],1)
            self.assertEqual(report['string_references'][0]['references'][0]['address'],hex(IMAGE_BASE+0x1020))
            self.assertEqual(path.read_bytes(),data)
            self.assertLess(len(json.dumps(report)),30000)
            with self.assertRaisesRegex(ValueError,'hash differs'):
                inspect(path,{**layout,'sha256':'wrong'})
            layout['tls_diagnostics']['samples'][0]['text']='ProtoSSL Secure State'
            with self.assertRaisesRegex(ValueError,'string bytes'):
                inspect(path,layout)


if __name__ == '__main__':
    unittest.main()
