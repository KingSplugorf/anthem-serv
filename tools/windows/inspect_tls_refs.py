"""Read-only code-reference report for the game TLS trust investigation.

Usage: py -3 inspect_tls_refs.py Anthem.exe --layout Anthem-tls-layout.json
Uses the previous report's SHA-256 and a few TLS string offsets. Exports bounded
code excerpts and candidate CA-loader-shaped wrappers; it does not execute,
patch, inject into, or load the executable. Candidates are not verified APIs.
"""
import argparse
from bisect import bisect_right
import hashlib
import json
import mmap
from pathlib import Path
import re
import struct

IMAGE_BASE = 0x140000000
STRING_NAMES = ("ProtoSSL Global State", "ProtoSSL Secure State", "Override_ProtoSSL_",
                "getCACertificates", "findCACertificates", "-----BEGIN CERTIFICATE-----")
LEA = re.compile(rb"[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d].{4}", re.DOTALL)
TRUE_WRAPPER = re.compile(rb"\x41\xb0\x01\xe9(.{4})", re.DOTALL)
FALSE_WRAPPER = re.compile(rb"\x45\x33\xc0\xe9(.{4})", re.DOTALL)


def pe_layout(data):
    def unpack(fmt, offset):
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(data):
            raise ValueError("Truncated PE metadata")
        return struct.unpack_from(fmt, data, offset)
    if data[:2] != b"MZ": raise ValueError("Not a PE file")
    pe, = unpack("<I", 0x3c)
    if data[pe:pe+4] != b"PE\0\0": raise ValueError("Invalid PE signature")
    machine, count = unpack("<HH", pe+4)
    optional_size, = unpack("<H", pe+20)
    optional = pe+24
    magic, = unpack("<H", optional)
    base, = unpack("<Q", optional+24)
    if machine != 0x8664 or magic != 0x20b or base != IMAGE_BASE or optional_size < 144:
        raise ValueError("Expected the previously inspected x64 game image at 0x140000000")
    sections = []
    for index in range(count):
        section = optional+optional_size+40*index
        virtual_size, rva, raw_size, raw_offset = unpack("<IIII", section+8)
        flags, = unpack("<I", section+36)
        if raw_offset+raw_size > len(data): raise ValueError("Invalid section bounds")
        sections.append(dict(rva=rva, virtual_size=virtual_size, raw_size=raw_size,
                             raw_offset=raw_offset, executable=bool(flags & 0x20000000)))
    exception_rva, exception_size = unpack("<II", optional+112+3*8)
    return sections, exception_rva, exception_size


def raw_to_rva(sections, offset):
    for section in sections:
        if section['raw_offset'] <= offset < section['raw_offset']+section['raw_size']:
            return section['rva'] + offset-section['raw_offset']
    raise ValueError(f"Offset {offset:#x} is not in a file-backed section")


def rva_to_raw(sections, rva, size=1):
    for section in sections:
        if section['rva'] <= rva and rva+size <= section['rva']+section['raw_size']:
            return section['raw_offset']+rva-section['rva']
    return None


def function_ranges(data, sections, rva, size):
    if not rva or not size: return []
    size -= size % 12
    if size > 12_000_000: raise ValueError("Exception table exceeds report limits")
    offset = rva_to_raw(sections, rva, size)
    if offset is None: return []
    return sorted((start, end) for start, end, _ in struct.iter_unpack('<III', data[offset:offset+size]) if 0 < start < end)


def context(data, sections, functions, starts, rva):
    raw = rva_to_raw(sections, rva)
    if raw is None: return dict(address=hex(IMAGE_BASE+rva), note='not file-backed')
    index = bisect_right(starts, rva)-1
    if index >= 0 and functions[index][0] <= rva < functions[index][1]:
        first, last = functions[index]
    else:
        first, last = rva, rva+512
    # Retain a bounded whole function where possible; otherwise retain its entry
    # and a separate excerpt at the reference site.
    chunks = []
    for begin, length in [(first, min(last-first, 4096)), (rva, min(last-rva, 512))]:
        for section in sections:
            if section['rva'] <= begin < section['rva']+section['raw_size']:
                length = min(length, section['rva']+section['raw_size']-begin)
                break
        offset = rva_to_raw(sections, begin, length)
        if offset is not None and not any(chunk['address'] == hex(IMAGE_BASE+begin) for chunk in chunks):
            chunks.append(dict(address=hex(IMAGE_BASE+begin), file_offset=hex(offset), bytes=data[offset:offset+length].hex()))
    return dict(address=hex(IMAGE_BASE+rva), function_start=hex(IMAGE_BASE+first),
                function_end=hex(IMAGE_BASE+last), excerpts=chunks)


def lea_references(data, sections, targets):
    found = {address: [] for address in targets}
    counts = {address: 0 for address in targets}
    for section in sections:
        first, last = section['raw_offset'], section['raw_offset']+section['raw_size']
        for match in LEA.finditer(data, first, last):
            rva = section['rva']+match.start()-first
            target = IMAGE_BASE+rva+7+struct.unpack_from('<i', data, match.start()+3)[0]
            if target in found:
                counts[target] += 1
                if len(found[target]) < 6: found[target].append(rva)
    return found, counts


def wrapper_pairs(data, sections):
    def wrappers(pattern):
        for section in sections:
            first, last = section['raw_offset'], section['raw_offset']+section['raw_size']
            for match in pattern.finditer(data, first, last):
                rva = section['rva']+match.start()-first
                yield rva, rva+8+struct.unpack('<i',match.group(1))[0]
    false = {}
    for rva, target in wrappers(FALSE_WRAPPER): false.setdefault(target, []).append(rva)
    result = []
    for rva, target in wrappers(TRUE_WRAPPER):
        for other in false.get(target, []):
            if abs(rva-other) <= 128 and rva_to_raw(sections, target) is not None:
                result.append(dict(true_wrapper=hex(IMAGE_BASE+rva), false_wrapper=hex(IMAGE_BASE+other), target_rva=target))
                if len(result) == 24: return result
    return result


def inspect(executable, layout):
    with open(executable,'rb') as source:
        digest=hashlib.file_digest(source,'sha256').hexdigest()
        if digest != layout.get('sha256'): raise ValueError("Executable hash differs from the layout report; use the same executable")
        with mmap.mmap(source.fileno(),0,access=mmap.ACCESS_READ) as data:
            sections, exception_rva, exception_size=pe_layout(data)
            functions=function_ranges(data,sections,exception_rva,exception_size)
            starts=[entry[0] for entry in functions]
            targets={}
            for sample in layout.get('tls_diagnostics',{}).get('samples',[]):
                text=sample['text']
                if text not in STRING_NAMES and not text.startswith('TLS/SSL certificate failure ('): continue
                offset=int(sample['file_offset'],16)
                expected=text.encode(sample['encoding'])
                if offset < 0 or data[offset:offset+len(expected)] != expected:
                    raise ValueError("TLS string bytes do not match the supplied report")
                targets[IMAGE_BASE+raw_to_rva(sections,offset)] = text
            if not targets: raise ValueError("No relevant TLS strings in layout; use the updated TLS layout report")
            references, counts=lea_references(data,sections,targets)
            strings=[dict(text=text,address=hex(address),reference_count=counts[address],
                          references=[context(data,sections,functions,starts,rva) for rva in references[address]])
                     for address,text in targets.items()]
            wrappers=[]
            for pair in wrapper_pairs(data,sections):
                target=pair.pop('target_rva')
                wrappers.append({**pair,'common_function':context(data,sections,functions,starts,target)})
            return dict(file=Path(executable).name,sha256=digest,string_references=strings,
                        candidate_boolean_wrappers=wrappers,
                        wrapper_candidate_limit_reached=len(wrappers) == 24,
                        warning='Read-only static candidates, not verified function identities. Do not call or patch these addresses based on this report alone.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable',type=Path)
    parser.add_argument('--layout',type=Path,default=Path('Anthem-tls-layout.json'))
    parser.add_argument('--output',type=Path,default=Path('Anthem-tls-code.json'))
    args=parser.parse_args()
    print('Reading TLS code references without executing or changing the game...',flush=True)
    try:
        result=inspect(args.executable,json.loads(args.layout.read_text(encoding='utf-8-sig')))
        args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    except (OSError,ValueError,KeyError,struct.error) as error:
        parser.exit(1,f'Inspection failed: {error}\n')
    print(f"{args.output}: {sum(s['reference_count'] for s in result['string_references'])} string references; {len(result['candidate_boolean_wrappers'])} wrapper candidates")


if __name__ == '__main__':
    main()
