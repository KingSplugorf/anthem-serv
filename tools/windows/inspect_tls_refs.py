"""Read-only code-reference report for the game TLS trust investigation.

Usage: py -3 inspect_tls_refs.py Anthem.exe --layout Anthem-tls-layout.json
Uses the previous report's SHA-256 and a few TLS string offsets. Alternatively,
--ca-loader follows PEM marker pointers and callers for the inspected build,
without needing the previous report. Exports bounded static candidates; it does
not execute, patch, inject into, or load the executable. Candidates are not APIs.
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
SUPPORTED_SHA256 = '63cbc85f17ebd5d9ede8d72cef07eff40945bf5000604b52d482812cbbcd0338'
STRING_NAMES = ("ProtoSSL Global State", "ProtoSSL Secure State", "Override_ProtoSSL_",
                "getCACertificates", "findCACertificates", "-----BEGIN CERTIFICATE-----")
LEA = re.compile(rb"[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d].{4}", re.DOTALL)
RIP_MEMORY = re.compile(rb"[\x48\x4c][\x8b\x8d][\x05\x0d\x15\x1d\x25\x2d\x35\x3d].{4}", re.DOTALL)
DIRECT_FLOW = re.compile(rb"[\xe8\xe9](.{4})", re.DOTALL)
TRUE_WRAPPER = re.compile(rb"(?:\x41\xb0\x01|\x41\xb8\x01\x00\x00\x00)\xe9(.{4})", re.DOTALL)
FALSE_WRAPPER = re.compile(rb"\x45[\x31\x33]\xc0\xe9(.{4})", re.DOTALL)
CA_MARKERS = (
    b'-----BEGIN CERTIFICATE-----', b'-----END CERTIFICATE-----',
    b'-----BEGIN X509 CERTIFICATE-----', b'-----END X509 CERTIFICATE-----',
    b'protossl: SSL Perf (CA load) %dms\n', b'added new certificate authority',
    b'ignoring redundant add of CA cert', b'failed to allocate memory for cert',
)
CA_FUNCTION_LIMIT = 24
CA_CALLER_DEPTH = 4
CA_BUNDLE_RANGE_LIMIT = 8192
# Fixed, file-backed neighborhoods containing the observed PEM parser, TLS
# constructor and global initializer, plus their original entry stubs. These
# are static executable bytes, never process memory or TLS session buffers.
CA_CODE_REGIONS = (
    ('original TLS entry region', 0x140820000, 0x80000),
    ('relocated TLS code region', 0x147D60000, 0x80000),
)
PEM_TABLE_ADDRESS, PEM_TABLE_SIZE = 0x143732520, 0x78
INDIRECT_FLOW = re.compile(rb'\xff[\x15\x25](.{4})', re.DOTALL)


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


def lea_references(data, sections, targets, pattern=LEA):
    found = {address: [] for address in targets}
    counts = {address: 0 for address in targets}
    for section in sections:
        first, last = section['raw_offset'], section['raw_offset']+section['raw_size']
        if not section.get('executable', True): continue
        cursor = first
        while (match := pattern.search(data, cursor, last)) is not None:
            rva = section['rva']+match.start()-first
            target = IMAGE_BASE+rva+7+struct.unpack_from('<i', data, match.start()+3)[0]
            if target in found:
                counts[target] += 1
                if len(found[target]) < 6: found[target].append(rva)
            cursor = match.start()+1
    return found, counts


def wrapper_pairs(data, sections):
    def wrappers(pattern):
        for section in sections:
            if not section.get('executable', True): continue
            first, last = section['raw_offset'], section['raw_offset']+section['raw_size']
            for match in pattern.finditer(data, first, last):
                rva = section['rva']+match.start()-first
                yield rva, rva+(match.end()-match.start())+struct.unpack('<i',match.group(1))[0]
    false = {}
    for rva, target in wrappers(FALSE_WRAPPER): false.setdefault(target, []).append(rva)
    result = []
    for rva, target in wrappers(TRUE_WRAPPER):
        for other in false.get(target, []):
            if abs(rva-other) <= 128 and rva_to_raw(sections, target) is not None:
                result.append(dict(true_wrapper=hex(IMAGE_BASE+rva), false_wrapper=hex(IMAGE_BASE+other), target_rva=target))
                if len(result) == 24: return result
    return result


def occurrences(data, value, limit):
    """Keep a bounded sample and a count, including overlapping occurrences."""
    result, count, offset = [], 0, 0
    while (offset := data.find(value, offset)) >= 0:
        count += 1
        if len(result) < limit: result.append(offset)
        offset += 1
    return result, count


def caller_references(data, sections, targets):
    """Raw E8/E9 candidates; instruction boundaries still need disassembly."""
    result = {target: [] for target in targets}
    counts = dict.fromkeys(targets, 0)
    for section in sections:
        if not section.get('executable', True): continue
        first, last = section['raw_offset'], section['raw_offset']+section['raw_size']
        # Do not use finditer: a candidate opcode may be inside a previous
        # candidate's displacement, so every byte must remain searchable.
        cursor = first
        while (match := DIRECT_FLOW.search(data, cursor, last)) is not None:
            rva = section['rva']+match.start()-first
            target = rva+5+struct.unpack('<i', match.group(1))[0]
            if target in result:
                counts[target] += 1
                if len(result[target]) < 6:
                    result[target].append((rva, 'call' if data[match.start()] == 0xe8 else 'jump'))
            cursor = match.start()+1
    return result, counts


def direct_jump_target(data, sections, rva):
    raw = rva_to_raw(sections, rva, 5)
    if raw is None or data[raw] != 0xe9: return None
    target = rva+5+struct.unpack_from('<i', data, raw+1)[0]
    if any(s.get('executable', False) and s['rva'] <= target < s['rva']+s['raw_size']
           for s in sections):
        return target
    return None


def ca_code_bundle(data, sections, functions):
    regions, slots, ranges = [], set(), []
    for label, address, size in CA_CODE_REGIONS:
        rva = address-IMAGE_BASE
        raw = rva_to_raw(sections, rva, size)
        section = next((s for s in sections if s['rva'] <= rva and
                        rva+size <= s['rva']+s['raw_size']), None)
        if raw is None or section is None or not section.get('executable', False):
            raise ValueError(f'Expected file-backed executable region missing at {address:#x}')
        content = data[raw:raw+size]
        regions.append(dict(label=label, address=hex(address), file_offset=hex(raw),
                            size=size, sha256=hashlib.sha256(content).hexdigest(),
                            bytes=content.hex()))
        ranges.extend((start, end) for start, end in functions if start < rva+size and end > rva)
        cursor = raw
        while (match := INDIRECT_FLOW.search(data, cursor, raw+size)) is not None:
            instruction = rva+match.start()-raw
            slot = instruction+6+struct.unpack('<i', match.group(1))[0]
            if rva_to_raw(sections, slot, 8) is not None:
                slots.add(slot)
            cursor = match.start()+1
    table_raw = rva_to_raw(sections, PEM_TABLE_ADDRESS-IMAGE_BASE, PEM_TABLE_SIZE)
    if table_raw is None:
        raise ValueError('Expected file-backed PEM marker table missing')
    # A slot contains a static import/pointer value, not a resolved runtime API.
    pointer_slots = []
    for slot in sorted(slots)[:512]:
        raw = rva_to_raw(sections, slot, 8)
        pointer_slots.append(dict(address=hex(IMAGE_BASE+slot),
                                  file_value=hex(struct.unpack_from('<Q', data, raw)[0])))
    unique_ranges = sorted(set(ranges))
    return dict(regions=regions, total_code_bytes=sum(r['size'] for r in regions),
                function_ranges=[dict(start=hex(IMAGE_BASE+start), end=hex(IMAGE_BASE+end))
                                 for start, end in unique_ranges[:CA_BUNDLE_RANGE_LIMIT]],
                function_ranges_limited=len(unique_ranges)>CA_BUNDLE_RANGE_LIMIT,
                pem_table=dict(address=hex(PEM_TABLE_ADDRESS),
                               bytes=data[table_raw:table_raw+PEM_TABLE_SIZE].hex()),
                indirect_pointer_slots=pointer_slots, pointer_slots_limited=len(slots)>512,
                warning='Bounded static file excerpts only. Import slots may be unresolved; runtime use and callable API addresses are unverified.')


def ca_loader_report(data, sections, functions):
    """Follow marker -> pointer table -> code -> callers, including jump stubs."""
    starts = [entry[0] for entry in functions]
    targets, markers = {}, []
    for marker in CA_MARKERS:
        # Embedded PEM certificates have a newline after BEGIN, while parser
        # delimiter literals end in NUL. Both can reveal CA-loading callers.
        needle = marker if marker.startswith(b'-----') else marker+b'\0'
        offsets, count = occurrences(data, needle, 8)
        entry = dict(text=marker.decode('ascii'), occurrences=count, locations=[])
        for offset in offsets:
            try:
                address = IMAGE_BASE+raw_to_rva(sections, offset)
            except ValueError:
                continue
            targets[address] = 'marker string'
            pointers, pointer_count = occurrences(data, struct.pack('<Q', address), 16)
            location = dict(address=hex(address), pointer_occurrences=pointer_count,
                            pointer_locations=[])
            for pointer in pointers:
                try:
                    pointer_rva = raw_to_rva(sections, pointer)
                except ValueError:
                    continue
                if pointer_rva % 8: continue
                location['pointer_locations'].append(hex(IMAGE_BASE+pointer_rva))
                # Include nearby table starts: the referenced marker need not
                # be the first member of a structure or the first table entry.
                for delta in range(-32, 17, 8):
                    alias = pointer_rva+delta
                    if rva_to_raw(sections, alias, 8) is not None:
                        targets.setdefault(IMAGE_BASE+alias, 'near marker pointer')
            entry['locations'].append(location)
        markers.append(entry)
    if not targets:
        raise ValueError('No certificate-loader markers found in file-backed sections')
    references, counts = lea_references(data, sections, targets, RIP_MEMORY)
    code, exported, truncated = [], set(), False

    def add(rva, reason, depth):
        nonlocal truncated
        candidate = context(data, sections, functions, starts, rva)
        first = candidate.get('function_start')
        # A nearby x64 unwind function range supplies boundaries, not identity.
        # A direct jump stub can have no unwind record; retain it in the graph
        # so callers of the original entry can still be found.
        index = bisect_right(starts, rva)-1
        bounded = index >= 0 and functions[index][0] <= rva < functions[index][1]
        key = first if bounded else candidate['address']
        if key in exported: return None
        if len(code) >= CA_FUNCTION_LIMIT:
            truncated = True
            return None
        exported.add(key)
        record = dict(reason=reason, caller_depth=depth, unwind_range_found=bounded,
                      code=candidate)
        code.append(record)
        if bounded: return int(first, 16)-IMAGE_BASE
        target = direct_jump_target(data, sections, rva)
        if target is not None:
            record['direct_jump_target'] = hex(IMAGE_BASE+target)
            return rva
        return None

    frontier = set()
    sites = []
    for address in targets:
        if not counts[address]: continue
        sites.append(dict(target=hex(address), target_kind=targets[address],
                          candidate_count=counts[address],
                          candidate_sites=[hex(IMAGE_BASE+rva) for rva in references[address]]))
        for rva in references[address]:
            first = add(rva, f'RIP-relative reference to {hex(address)}', 0)
            if first is not None: frontier.add(first)
    edges, walked = [], set()
    for depth in range(1, CA_CALLER_DEPTH+1):
        frontier -= walked
        if not frontier: break
        walked.update(frontier)
        callers, caller_counts = caller_references(data, sections, frontier)
        next_frontier = set()
        for target in sorted(frontier):
            edges.append(dict(target=hex(IMAGE_BASE+target), candidate_count=caller_counts[target],
                              candidates=[dict(address=hex(IMAGE_BASE+rva), kind=kind)
                                          for rva, kind in callers[target]]))
            for rva, kind in callers[target]:
                first = add(rva, f'{kind} candidate to {hex(IMAGE_BASE+target)}', depth)
                if first is not None: next_frontier.add(first)
        frontier = next_frontier
    return dict(markers=markers, reference_sites=sites, caller_edges=edges,
                caller_depth_limit=CA_CALLER_DEPTH,
                candidate_functions=code, function_limit=CA_FUNCTION_LIMIT,
                function_limit_reached=truncated,
                warning='Static byte-pattern candidates may overlap data or instruction operands. Function identities, calling conventions and runtime use are unverified. Do not invoke these addresses.')


def inspect_ca(executable, *, include_code_bundle=False):
    with open(executable, 'rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != SUPPORTED_SHA256:
            raise ValueError('CA-loader inspection supports only the previously inspected Anthem.exe SHA-256')
        with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
            sections, exception_rva, exception_size = pe_layout(data)
            functions = function_ranges(data, sections, exception_rva, exception_size)
            report = dict(format_version=2, file=Path(executable).name, sha256=digest,
                          ca_loader=ca_loader_report(data, sections, functions))
            if include_code_bundle:
                report['code_bundle'] = ca_code_bundle(data, sections, functions)
            return report


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
    parser.add_argument('--ca-loader', action='store_true',
                        help='Follow CA marker pointer tables and callers; does not require --layout')
    parser.add_argument('--ca-code-bundle', action='store_true',
                        help='Include 1 MiB of fixed TLS code excerpts and metadata; implies --ca-loader')
    args=parser.parse_args()
    ca_mode = args.ca_loader or args.ca_code_bundle
    print('Reading CA marker tables and caller candidates (read-only)...' if ca_mode else
          'Reading TLS code references without executing or changing the game...',flush=True)
    try:
        if args.output.resolve() == args.executable.resolve() or args.output.resolve() == args.layout.resolve():
            raise ValueError('Output must differ from the executable and input layout report')
        result=(inspect_ca(args.executable, include_code_bundle=args.ca_code_bundle) if ca_mode else
                inspect(args.executable,json.loads(args.layout.read_text(encoding='utf-8-sig'))))
        args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    except (OSError,ValueError,KeyError,struct.error) as error:
        parser.exit(1,f'Inspection failed: {error}\n')
    if ca_mode:
        print(f"{args.output}: {len(result['ca_loader']['candidate_functions'])} static function candidates; no game files changed")
        if args.ca_code_bundle:
            print(f"Included {result['code_bundle']['total_code_bytes']} static TLS code bytes; keep this report for analysis")
    else:
        print(f"{args.output}: {sum(s['reference_count'] for s in result['string_references'])} string references; {len(result['candidate_boolean_wrappers'])} wrapper candidates")


if __name__ == '__main__':
    main()
