import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from windows import inspect_tls_refs as inspector
from windows.inspect_tls_refs import (IMAGE_BASE, ca_loader_report, caller_references,
                                     ca_code_bundle, context, inspect, inspect_ca,
                                     lea_references, wrapper_pairs)


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

    def test_ca_wrapper_pairs_accept_full_register_boolean_and_both_xor_encodings(self):
        data=bytearray(0x800)
        data[0x220:0x22b]=b'\x41\xb8\x01\x00\x00\x00\xe9'+struct.pack('<i',0x1200-(0x1020+11))
        data[0x230:0x238]=b'\x45\x31\xc0\xe9'+struct.pack('<i',0x1200-(0x1030+8))
        result=wrapper_pairs(data,[self.section()])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['target_rva'],0x1200)

    def test_ca_report_follows_pointer_table_and_two_caller_levels(self):
        data=bytearray(0x1000)
        sections=[dict(rva=0x1000,raw_offset=0x200,raw_size=0x600,executable=True),
                  dict(rva=0x2000,raw_offset=0x800,raw_size=0x800,executable=False)]
        marker=b'-----BEGIN CERTIFICATE-----\0'
        data[0x900:0x900+len(marker)]=marker
        # The marker is the second pointer, while code loads the table start.
        struct.pack_into('<Q',data,0x828,IMAGE_BASE+0x2100)
        data[0x240:0x247]=b'\x4c\x8b\x05'+struct.pack('<i',0x2020-(0x1040+7))
        # Caller 1 parses PEM; caller 2 is a possible public wrapper.
        data[0x304:0x309]=b'\xe8'+struct.pack('<i',0x1040-(0x1104+5))
        data[0x324:0x329]=b'\xe9'+struct.pack('<i',0x1100-(0x1124+5))
        # An apparent call in non-executable data must not be treated as code.
        data[0xa00:0xa05]=b'\xe8'+struct.pack('<i',0x1040-(0x2200+5))
        functions=[(0x1040,0x1050),(0x1100,0x1110),(0x1120,0x1130)]
        before=bytes(data)
        report=ca_loader_report(data,sections,functions)
        self.assertEqual(report['markers'][0]['locations'][0]['pointer_locations'],[hex(IMAGE_BASE+0x2028)])
        self.assertEqual(report['reference_sites'][0]['target'],hex(IMAGE_BASE+0x2020))
        self.assertEqual([c['caller_depth'] for c in report['candidate_functions']],[0,1,2])
        self.assertEqual(report['caller_edges'][0]['candidate_count'],1)
        self.assertEqual(report['caller_edges'][1]['candidates'][0]['kind'],'jump')
        self.assertEqual(bytes(data),before)
        self.assertLess(len(json.dumps(report)),10000)

    def test_flow_search_keeps_overlapping_opcode_candidates(self):
        data=bytearray(0x800)
        # The real call opcode is inside a spurious candidate's displacement.
        data[0x220:0x227]=b'\xe8\x00\xe8'+struct.pack('<i',0x1200-(0x1022+5))
        references,counts=caller_references(data,[self.section()],{0x1200})
        self.assertEqual(counts[0x1200],1)
        self.assertEqual(references[0x1200],[(0x1022,'call')])

    def test_ca_walk_follows_callers_through_jump_stub_without_unwind_record(self):
        data=bytearray(0x1000)
        sections=[dict(rva=0x1000,raw_offset=0x200,raw_size=0x600,executable=True),
                  dict(rva=0x2000,raw_offset=0x800,raw_size=0x800,executable=False)]
        marker=b'-----BEGIN CERTIFICATE-----\0'
        data[0x900:0x900+len(marker)]=marker
        data[0x240:0x247]=b'\x48\x8d\x0d'+struct.pack('<i',0x2100-(0x1040+7))
        data[0x300:0x305]=b'\xe9'+struct.pack('<i',0x1040-(0x1100+5))
        data[0x404:0x409]=b'\xe8'+struct.pack('<i',0x1100-(0x1204+5))
        data[0x444:0x449]=b'\xe9'+struct.pack('<i',0x1200-(0x1244+5))
        report=ca_loader_report(data,sections,[(0x1040,0x1050),(0x1200,0x1210),(0x1240,0x1250)])
        candidates=report['candidate_functions']
        self.assertEqual([c['caller_depth'] for c in candidates],[0,1,2,3])
        self.assertFalse(candidates[1]['unwind_range_found'])
        self.assertEqual(candidates[1]['direct_jump_target'],hex(IMAGE_BASE+0x1040))
        self.assertEqual(candidates[2]['code']['function_start'],hex(IMAGE_BASE+0x1200))

    def test_ca_search_also_finds_embedded_pem_with_newline_after_header(self):
        data=bytearray(0x800)
        pem=b'-----BEGIN CERTIFICATE-----\nBASE64\n-----END CERTIFICATE-----\0'
        data[0x500:0x500+len(pem)]=pem
        data[0x220:0x227]=b'\x48\x8d\x0d'+struct.pack('<i',0x1300-(0x1020+7))
        report=ca_loader_report(data,[self.section()],[(0x1020,0x1030)])
        self.assertEqual(report['markers'][0]['occurrences'],1)
        self.assertEqual(report['reference_sites'][0]['target'],hex(IMAGE_BASE+0x1300))
        self.assertEqual(report['candidate_functions'][0]['code']['function_start'],hex(IMAGE_BASE+0x1020))

    def test_code_bundle_exports_file_ranges_unwind_metadata_and_indirect_slots(self):
        data=bytearray(0x900)
        sections=[dict(rva=0x1000,raw_offset=0x200,raw_size=0x100,executable=True),
                  dict(rva=0x2000,raw_offset=0x500,raw_size=0x100,executable=True),
                  dict(rva=0x3000,raw_offset=0x800,raw_size=0x100,executable=False)]
        data[0x520:0x526]=b'\xff\x15'+struct.pack('<i',0x3000-(0x2020+6))
        struct.pack_into('<Q',data,0x800,IMAGE_BASE+0x1000)
        regions=(('original',IMAGE_BASE+0x1000,0x100),('relocated',IMAGE_BASE+0x2000,0x100))
        original=bytes(data)
        with patch.object(inspector,'CA_CODE_REGIONS',regions), \
             patch.object(inspector,'PEM_TABLE_ADDRESS',IMAGE_BASE+0x3020):
            bundle=ca_code_bundle(data,sections,[(0x1000,0x1010),(0x2010,0x2030),(0x4000,0x4100)])
        self.assertEqual(bundle['total_code_bytes'],512)
        self.assertEqual(len(bundle['function_ranges']),2)
        self.assertEqual(bytes.fromhex(bundle['regions'][1]['bytes']),data[0x500:0x600])
        self.assertEqual(bundle['regions'][1]['sha256'],hashlib.sha256(data[0x500:0x600]).hexdigest())
        self.assertEqual(bundle['indirect_pointer_slots'],[dict(address=hex(IMAGE_BASE+0x3000),file_value=hex(IMAGE_BASE+0x1000))])
        self.assertEqual(len(bytes.fromhex(bundle['pem_table']['bytes'])),0x78)
        self.assertEqual(bytes(data),original)

    def test_code_bundle_rejects_nonexecutable_or_incomplete_ranges(self):
        data=bytearray(0x800)
        regions=(('original',IMAGE_BASE+0x1000,0x100),)
        with patch.object(inspector,'CA_CODE_REGIONS',regions):
            for section in ({**self.section(),'executable':False},
                            {**self.section(),'raw_size':0x80,'executable':True}):
                with self.assertRaisesRegex(ValueError,'executable region missing'):
                    ca_code_bundle(data,[section],[])

    def test_ca_report_stops_exporting_at_function_limit(self):
        data=bytearray(0x800)
        marker=b'-----BEGIN CERTIFICATE-----\0'
        data[0x500:0x500+len(marker)]=marker
        for offset in (0x220,0x240):
            rva=0x1000+offset-0x200
            data[offset:offset+7]=b'\x48\x8d\x0d'+struct.pack('<i',0x1300-(rva+7))
        with patch.object(inspector,'CA_FUNCTION_LIMIT',1):
            report=ca_loader_report(data,[self.section()],[(0x1020,0x1030),(0x1040,0x1050)])
        self.assertEqual(len(report['candidate_functions']),1)
        self.assertTrue(report['function_limit_reached'])

    def test_ca_inspection_rejects_unknown_executable_without_changing_it(self):
        with tempfile.TemporaryDirectory() as folder:
            executable=Path(folder)/'Anthem.exe'
            executable.write_bytes(b'unsupported executable')
            with self.assertRaisesRegex(ValueError,'SHA-256'):
                inspect_ca(executable)
            self.assertEqual(executable.read_bytes(),b'unsupported executable')

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
        marker=b'-----BEGIN CERTIFICATE-----\0'
        data[0x600:0x600+len(marker)]=marker
        struct.pack_into('<Q',data,0x680,IMAGE_BASE+0x1400)
        data[0x240:0x247]=b'\x48\x8d\x15'+struct.pack('<i',0x1480-(0x1040+7))
        struct.pack_into('<II',data,0x98+112+3*8,0x1600,12)
        struct.pack_into('<III',data,0x800,0x1040,0x1050,0)
        layout=dict(sha256=hashlib.sha256(data).hexdigest(),tls_diagnostics=dict(samples=[dict(text=text,file_offset='0x500',encoding='ascii')]))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'Anthem.exe'; path.write_bytes(data)
            report=inspect(path,layout)
            self.assertEqual(report['string_references'][0]['reference_count'],1)
            self.assertEqual(report['string_references'][0]['references'][0]['address'],hex(IMAGE_BASE+0x1020))
            self.assertEqual(path.read_bytes(),data)
            self.assertLess(len(json.dumps(report)),30000)
            with patch.object(inspector,'SUPPORTED_SHA256',layout['sha256']):
                ca_report=inspect_ca(path)
            self.assertEqual(ca_report['sha256'],layout['sha256'])
            self.assertTrue(ca_report['ca_loader']['candidate_functions'][0]['unwind_range_found'])
            self.assertEqual(path.read_bytes(),data)
            with self.assertRaisesRegex(ValueError,'hash differs'):
                inspect(path,{**layout,'sha256':'wrong'})
            layout['tls_diagnostics']['samples'][0]['text']='ProtoSSL Secure State'
            with self.assertRaisesRegex(ValueError,'string bytes'):
                inspect(path,layout)


if __name__ == '__main__':
    unittest.main()
