"""Read-only AI, damage-filter and attack-code report for the supported game.

No debugger attachment, remote execution, memory writes or save changes.
Exports bounded code windows; these are not verified function boundaries.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import struct
import sys
import time

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS/'combat_patches/ai'))
from process_memory import open_memory, read_at
from windows_memory import modules
import player_list

BASE = 0x140000000
SERVER_CHARACTER = 0x143A75900
AI_COMPONENT = 0x143ACD3D0
HOSTILITY_COMPONENT = 0x143BCA3D0
GEAR_COMPONENT, ACTION_GEAR = 0x143A876C0, 0x143A5BF40
HEALTH_COMPONENTS = (0x143A88538, 0x143A88050)
PLAYER_HEALTH = 0x143A88050
CLIENT_PLAYER_HEALTH = 0x143A8CEC0
SERVER_SHIELD, CLIENT_SHIELD = 0x143A76AE0, 0x143A71690
RECORDS_POINTER, RECORD_SIZE, RECORD_COUNT = 0x153737618, 0x50, 128
RECORD = struct.Struct('<QQfffffBB2xQfffffIIf')
NATIVE = {
    'generic_entity_damage': 0x140C86EF0,
    'damage_dispatch': 0x140C15020,
    'damage_cone_action': 0x1408FCD10,
    'apply_damage_action': 0x1408FB780,
    'compute_damage_thunk': 0x140C162F0,
    'compute_damage': 0x1489308C0,
    'team_filter': 0x140D32920,
    'shield_filter': 0x140C25B60,
    'client_apply_damage': 0x140CBA0C0,
    'client_apply_damage_body': 0x148B365B0,
    'impact_stream_parser': 0x140B3CB00,
    'projectile_behavior_update': 0x148C949D0,
    'server_weapon_update': 0x14879D8C0,
    'client_weapon_update': 0x1480E8810,
}


class View:
    def __init__(self, read): self.reader, self.calls, self.bytes = read, 0, 0
    def read(self, address, size):
        if (not 0x10000 <= address < address+size <= 1 << 47 or size > 8192
                or self.calls >= 20000 or self.bytes+size > 1 << 20):
            raise OSError('Read range or diagnostic budget exceeded')
        self.calls += 1
        self.bytes += size
        data = self.reader(address, size)
        if len(data) != size: raise OSError('Partial process read')
        return data
    def qword(self, address): return struct.unpack('<Q', self.read(address, 8))[0]
    def u32(self, address): return struct.unpack('<I', self.read(address, 4))[0]
    def f32(self, address): return struct.unpack('<f', self.read(address, 4))[0]
    def text(self, address): return self.read(address, 96).split(b'\0')[0].decode('utf-8', errors='replace')


def safe(function):
    try: return function()
    except (OSError, ValueError, struct.error) as error: return {'error': str(error)}


def gear(view, component):
    first, last = view.qword(component+0x38), view.qword(component+0x40)
    if last < first or (last-first) % 0x88 or (last-first)//0x88 > 64:
        raise OSError('Unsupported gear slot array')
    result = {'active_slot':view.u32(component+0x774),'slots':[]}
    for address in range(first,last,0x88):
        entry = {'slot':view.u32(address),'entity':view.qword(address+0x20)}
        def behaviors():
            entity=entry['entity']
            if view.qword(entity) != ACTION_GEAR:
                return {'error':'Gear entity has another type'}
            rows=[]
            for offset in range(0x2B8,0x2D8,8):
                pointer=view.qword(entity+offset)
                info={'offset':offset,'address':pointer}
                if pointer:
                    def details():
                        table,owner=view.qword(pointer),view.qword(pointer+0x10)
                        detail={'table':table,'owner':owner,'owned':0<=owner-entity<0x460 and 0x143660000<=table<0x1442F0000}
                        if detail['owned']:
                            detail.update(vf20=view.qword(table+0x20),vf30=view.qword(table+0x30))
                        return detail
                    info['details']=safe(details)
                rows.append(info)
            return rows
        entry['behaviors']=safe(behaviors)
        result['slots'].append(entry)
    return result


def actor(view, entity):
    report = {'entity': entity, 'table': view.qword(entity)}
    if report['table'] != SERVER_CHARACTER:
        return {**report, 'error': 'Entity is not a live supported server character'}
    report['name'] = safe(lambda: view.text(view.qword(view.qword(entity+0x320)+0x20)))
    report['server_player'] = view.qword(entity+0x2B8)
    report['position'] = safe(lambda: struct.unpack('<3f', view.read(entity+0x1B0, 12)))
    block = view.qword(entity+0x58)
    count, first, second = view.read(block+8, 3)
    report['transform_position'] = safe(lambda: struct.unpack('<3f', view.read(block+(first+2*second)*32+0x40, 12)))
    report['components'] = []
    for index in range(count):
        def component():
            address = view.qword(block+0x20+index*0x20)
            if not address: return None
            table = view.qword(address)
            info = {'address': address, 'table': table}
            if table == HOSTILITY_COMPONENT:
                begin, end = view.qword(address+0x58), view.qword(address+0x60)
                info['default_group'] = view.u32(address+0x78)
                info['effective_group'] = view.u32(begin) if begin and begin != end else info['default_group']
            if table in HEALTH_COMPONENTS:
                info['health'], info['maximum'] = struct.unpack('<ff', view.read(address+0x20, 8))
                info['state'] = view.read(address+0x158, 1)[0]
                info['immortal'] = view.read(view.qword(address+0x10)+0x12F, 1)[0]
            if table == AI_COMPONENT:
                info['state'] = view.u32(address+0xC0)
                info['raw_component'] = view.read(address, 512).hex()
            if table == GEAR_COMPONENT:
                info['gear'] = safe(lambda: gear(view,address))
            def type_name():
                data = view.qword(address+0x10)
                type_info = view.qword(data+0x10)
                return view.text(view.qword(view.qword(type_info)+8))
            info['data_type_name_candidate'] = safe(type_name)
            return info
        info = safe(component)
        if info: report['components'].append(info)
    return report


def code_windows(view, addresses, output):
    pending = [(address, 0) for address in addresses]
    while pending and len(output) < 48:
        address, depth = pending.pop(0)
        if str(address) in output: continue
        entry = {'address': address}
        output[str(address)] = entry
        try:
            code = view.read(address, 4096)
            entry['bytes'] = code.hex()
            target = None
            if code[:1] == b'\xe9': target = address+5+struct.unpack('<i', code[1:5])[0]
            elif code[:2] == b'\xff\x25': target = view.qword(address+6+struct.unpack('<i', code[2:6])[0])
            if target and BASE+0x1000 <= target < 0x154000000 and depth < 2:
                entry['jump_target'] = target
                pending.append((target, depth+1))
        except OSError as error: entry['error'] = str(error)


def client_position(view, health):
    if not health or view.qword(health) != CLIENT_PLAYER_HEALTH:
        raise OSError('No validated cached client-player health component')
    block = view.qword(health+8)
    entity = view.qword(block)
    if view.qword(entity) != 0x143A6FF30 or view.qword(entity+0x58) != block:
        raise OSError('Client-player owner links changed')
    count, first, second = view.read(block+8,3)
    if not any(view.qword(block+0x20+index*0x20) == health for index in range(count)):
        raise OSError('Client-player health is not owned by this character')
    return {'health': health, 'entity': entity,
            'position': struct.unpack('<3f',view.read(block+(first+2*second)*32+0x40,12))}


def player_health(view):
    """Read fresh owner links each time; client health layout remains raw evidence."""
    def server():
        entity = view.qword(0x142B2ABF0)
        if view.qword(entity) != SERVER_CHARACTER or not view.qword(entity+0x2B8):
            raise OSError('Cached entity is not a supported player character')
        block = view.qword(entity+0x58)
        if view.qword(block) != entity:
            raise OSError('Server-player component owner changed')
        result = {'entity': entity, 'components': []}
        for index in range(view.read(block+8,1)[0]):
            address = view.qword(block+0x20+index*0x20)
            if not address: continue
            table = view.qword(address)
            if table not in (PLAYER_HEALTH, SERVER_SHIELD): continue
            if view.qword(address+8) != block: continue
            entry = {'address': address, 'table': table}
            if table == PLAYER_HEALTH:
                entry['health'], entry['maximum'] = struct.unpack('<ff',view.read(address+0x20,8))
                entry['ratio'] = view.f32(address+0x128)
                entry['state'] = view.read(address+0x158,1)[0]
                entry['shield_interface'] = view.qword(address+0x160)
            else:
                state = view.qword(address+0x50)
                entry['state_pointer'] = state
                entry['raw_state'] = safe(lambda: view.read(state,64).hex()) if state else None
            result['components'].append(entry)
        return result

    def client():
        health = view.qword(0x142B2ABE8)
        result = client_position(view,health)
        # Do not label unverified client fields as current/max health. The
        # native getter/setter code is exported separately for layout analysis.
        result['raw_health_component'] = safe(lambda: view.read(health,512).hex())
        block = view.qword(health+8)
        result['shields'] = []
        for index in range(view.read(block+8,1)[0]):
            address = view.qword(block+0x20+index*0x20)
            if not address or view.qword(address) != CLIENT_SHIELD or view.qword(address+8) != block:
                continue
            state = view.qword(address+0x58)
            result['shields'].append({'address':address, 'state_pointer':state,
                'raw_state':safe(lambda: view.read(state,64).hex()) if state else None})
        return result
    return {'server': safe(server), 'client': safe(client)}


def watch_health(view, seconds, *, clock=time.monotonic, pause=time.sleep):
    if not math.isfinite(seconds) or not 0 <= seconds <= 30:
        raise ValueError('Health recording must last between 0 and 30 seconds')
    start = clock()
    samples = []
    # The count cap also bounds work if the clock moves or a test clock stalls.
    for index in range(121):
        samples.append({'elapsed':round(clock()-start,3), **player_health(view)})
        remaining = seconds-(clock()-start)
        if remaining <= 0: break
        pause(min(0.25,remaining))
    return samples


def collect(read, watch_seconds=0):
    view = View(read)
    report = {'schema': 1, 'captured_utc': datetime.now(timezone.utc).isoformat(),
              'native_entries': NATIVE, 'code': {}, 'actors': [],
              'limitations': ['Code windows and raw slots do not establish function/table boundaries.',
                              'Actors can change while this read-only snapshot is collected.']}
    # Read original game routines first, before potentially changing world data.
    code_windows(view, NATIVE.values(), report['code'])
    report['player_health_samples'] = watch_health(view,watch_seconds)
    report['players'] = safe(lambda: player_list.server_players(view))
    report['player_slots'] = safe(lambda: struct.unpack('<3Q', view.read(0x142B2ABE0, 24)))
    report['weapon_counters'] = safe(lambda: struct.unpack('<IIf', view.read(0x1537377D0, 12)))
    report['ai_shot_counters'] = safe(lambda: struct.unpack('<IIfI', view.read(0x1537376E0, 16)))
    report['player_damage_bonus'] = safe(lambda: view.f32(0x1537377E8))
    report['server_time'] = safe(lambda: struct.unpack('<d', view.read(view.qword(view.qword(0x1446CECA0)+0x18)+0x10, 8))[0])
    report['perception_slots'] = safe(lambda: {name: view.qword(slot) for name, slot in (
        ('AITarget', 0x143ACF790), ('HasEnemies', 0x143AD02B8), ('LOSToTarget', 0x143AD0058),
        ('WantsToAttack', 0x143ACF7D8), ('RangeToTarget', 0x143AD0240), ('TargetIsPlayer', 0x143AD0080))})
    report['records'] = []
    entities = set()
    players = report['players']
    if isinstance(players, list): entities.update(row[3] for row in players if row[3])
    slots = report['player_slots']
    if isinstance(slots, tuple) and player_list.pointer(slots[2]): entities.add(slots[2])
    report['client_player'] = safe(lambda: client_position(view,slots[1])) if isinstance(slots,tuple) else {'error':'Player slots unavailable'}
    try:
        records = view.qword(RECORDS_POINTER)
        report['records_pointer'] = records
        if records:
            for index in range(RECORD_COUNT):
                fields = RECORD.unpack(view.read(records+index*RECORD_SIZE, RECORD_SIZE))
                entity, target = fields[:2]
                if not entity: continue
                entry = dict(zip(('entity','target','since','seen','evaluated','forget','next',
                    'visible','state','candidate','sight','spot_distance','vitality','damaged',
                    'alerted','rays','clear','gap'), fields))
                report['records'].append(entry)
                now = report['server_time']
                if isinstance(now, (int,float)) and math.isfinite(fields[4]) and 0 <= now-fields[4] <= 5 and len(entities) < 32:
                    entities.add(entity)
                    if target: entities.add(target)
    except OSError as error: report['records_error'] = str(error)
    report['actors'] = [safe(lambda entity=entity: actor(view, entity)) for entity in sorted(entities)[:32]]
    client = report['client_player']
    if 'position' in client:
        for info in report['actors']:
            if info.get('entity') == slots[2] and isinstance(info.get('position'),tuple):
                report['client_server_position_gap'] = math.dist(client['position'],info['position'])
    report['component_methods'] = []
    additional, health_methods = set(), set()
    # These are bounded candidate slots, including the otherwise omitted
    # SetHealth/update region. No table entry is called or replaced.
    report['health_method_tables'] = []
    for table in (*HEALTH_COMPONENTS, CLIENT_PLAYER_HEALTH):
        def health_table():
            values = struct.unpack('<64Q',view.read(table,512))
            for offset in (0x100,0x108,0x110,0x118,0x140,0x150):
                address = values[offset//8]
                if BASE+0x1000 <= address < 0x154000000: health_methods.add(address)
            return list(values)
        report['health_method_tables'].append({'table':table,'candidate_slots':safe(health_table)})
    for info in report['actors']:
        for component in info.get('components',[]):
            equipped=component.get('gear',{})
            for slot in equipped.get('slots',[]):
                behaviors=slot.get('behaviors')
                if not isinstance(behaviors,list): continue
                for behavior in behaviors:
                    detail=behavior.get('details',{})
                    additional.update(detail[name] for name in ('vf20','vf30') if
                        name in detail and BASE+0x1000<=detail[name]<0x154000000)
            name = component.get('data_type_name_candidate')
            if component.get('table') != AI_COMPONENT and not (
                    isinstance(name,str) and any(word in name.lower() for word in ('interact','damage','armor','heal','pickup','regen'))):
                continue
            table = component['table']
            if any(method['table'] == table for method in report['component_methods']): continue
            def methods():
                values = struct.unpack('<32Q',view.read(table,32*8))
                additional.update(address for address in values if BASE+0x1000 <= address < 0x154000000)
                return list(values)
            report['component_methods'].append({'table':table,'data_type_name_candidate':name,'candidate_slots':safe(methods)})
    code_windows(view, sorted(health_methods), report['code'])
    code_windows(view, sorted(additional), report['code'])
    report['read_budget_used'] = {'calls': view.calls, 'bytes': view.bytes}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--watch-seconds', type=float, default=0,
                        help='Record player health every 0.25 s for up to 30 seconds while collecting a pickup')
    args = parser.parse_args()
    if not math.isfinite(args.watch_seconds) or not 0 <= args.watch_seconds <= 30:
        parser.error('--watch-seconds must be between 0 and 30')
    if os.name != 'nt' or args.pid <= 0: parser.error('Use a positive running game PID on native 64-bit Windows')
    base, image = next(modules(args.pid))
    if base != BASE or Path(image).name.lower() not in ('anthem.exe','dylan.main_win64_retail.exe'):
        parser.error('Unsupported game image/base')
    with open_memory(args.pid, 'rb') as memory:
        if args.watch_seconds:
            print(f'Recording for {args.watch_seconds:g} seconds: switch to the game and collect a health pickup.',flush=True)
        report = collect(lambda address, size: read_at(memory, address, size),args.watch_seconds)
    report.update(pid=args.pid, image=image, image_base=base)
    output = args.output or Path(f'combat-internals-{args.pid}-{datetime.now():%Y%m%d-%H%M%S}.json')
    with output.open('x', encoding='utf-8') as destination: json.dump(report, destination, indent=2)
    print(f'Combat internals report: {output.resolve()}')


if __name__ == '__main__': main()
