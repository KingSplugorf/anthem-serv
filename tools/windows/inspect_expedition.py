"""Read-only export of the active expedition executor and candidate methods.

Does not attach a debugger, execute remote code, patch memory or edit saves.
Raw table slots and code windows do not establish function/table boundaries.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from process_memory import open_memory, read_at
from windows_memory import modules

BASE = 0x140000000
MANAGER = 0x1446C80E0
FREEPLAY_TABLE = 0x143A49910
FREEPLAY_LEAVE = 0x1481F1460


def collect(read):
    def qword(address):
        return struct.unpack('<Q', read(address, 8))[0]
    manager = qword(MANAGER)
    executor = qword(manager + 0x48) if manager else 0
    table = qword(executor) if executor else 0
    report = {'schema': 1, 'manager': manager, 'executor': executor, 'executor_table': table,
              'tables': [], 'code': {}, 'limitations': [
                  'Raw candidate slots can cross a table boundary.',
                  'Code windows are bounded byte snapshots, not verified function bodies.']}
    if executor:
        first, last = qword(executor + 0x100), qword(executor + 0x108)
        report['expeditions'] = {'first': first, 'last': last,
            'count': (last-first)//0x48 if last >= first and (last-first) % 0x48 == 0 else None}
    addresses = {FREEPLAY_LEAVE}
    for vtable in sorted({table, FREEPLAY_TABLE} - {0}):
        entry = {'address': vtable, 'candidate_slots': []}
        try:
            values = struct.unpack('<32Q', read(vtable, 32*8))
            for index, address in enumerate(values):
                entry['candidate_slots'].append({'offset': index*8, 'value': address})
                if BASE + 0x1000 <= address < 0x150000000:
                    addresses.add(address)
        except OSError as error:
            entry['error'] = str(error)
        report['tables'].append(entry)
    pending = [(address, 0) for address in sorted(addresses)]
    while pending and len(report['code']) < 100:
        address, depth = pending.pop(0)
        if str(address) in report['code']:
            continue
        entry = {'address': address}
        report['code'][str(address)] = entry
        try:
            code = read(address, 768)
            entry['bytes'] = code.hex()
            if code[:1] == b'\xe9' and depth < 2:
                target = address + 5 + struct.unpack('<i', code[1:5])[0]
                if BASE + 0x1000 <= target < 0x150000000:
                    entry['jump_target'] = target
                    pending.append((target, depth+1))
        except OSError as error:
            entry['error'] = str(error)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if os.name != 'nt' or args.pid <= 0:
        parser.error('Use a positive running game PID on native 64-bit Windows')
    base, image = next(modules(args.pid))
    if base != BASE or Path(image).name.lower() not in ('anthem.exe', 'dylan.main_win64_retail.exe'):
        parser.error('Unsupported game image/base')
    with open_memory(args.pid, 'rb') as memory:
        report = collect(lambda address, size: read_at(memory, address, size))
    report.update(pid=args.pid, image=image, image_base=base)
    output = args.output or Path(f'expedition-executor-{args.pid}-{datetime.now():%Y%m%d-%H%M%S}.json')
    with output.open('x', encoding='utf-8') as destination:
        json.dump(report, destination, indent=2)
    print(f"Executor table: {report['executor_table']:#x}; report: {output.resolve()}")


if __name__ == '__main__':
    main()
