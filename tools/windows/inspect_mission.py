"""Read-only mission-launch report: py -3 tools/windows/inspect_mission.py --pid PID.

Reads bounded logs and the live loopback BIGS profile. Does not attach a debugger,
change process memory, edit saves, or read game files/certificate keys.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mission_hooks import ATTRIBUTES, parse_attributes

TAIL_BYTES = 4 << 20
FREEPLAY_QUEST, FREEPLAY_DEFINITION = 1401555469, 1270939482
FREEPLAY_GATE_DEFINITION, FREEPLAY_GATE_PROPERTY = 1435527462, 1418391426


def tail(path):
    try:
        with path.open('rb') as source:
            source.seek(0, 2)
            size = source.tell()
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            source.seek(max(0, size - TAIL_BYTES))
            data = source.read(TAIL_BYTES)
        if size > TAIL_BYTES:
            data = data.partition(b'\n')[2]
        return {'exists': True, 'size_bytes': size, 'modified_utc': modified, 'tail_truncated': size > TAIL_BYTES}, data.decode('utf-8-sig', errors='replace').splitlines()
    except OSError as error:
        return {'exists': False, 'error': str(error)}, []


def fetch(route):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8080' + route, timeout=4) as response:
        body = response.read((4 << 20) + 1)
    if len(body) > 4 << 20:
        raise ValueError('BIGS response exceeds report limit')
    return json.loads(body)


def latest_setup(lines):
    block, latest = None, None
    for line in lines:
        if re.match(r'^\S+ (?:<-|->|=>) ', line):
            if block is not None:
                latest = block
            block = [] if '-> Notification 0x0004/0x0014 ' in line else None
        elif block is not None and len(block) < 250:
            block.append(line[:16000] if line.startswith('PROS = ') else line[:1000])
    if block is not None:
        latest = block
    if latest is None:
        return None
    text = '\n'.join(latest)
    def number(pattern):
        match = re.search(pattern, text)
        return int(match[1]) if match else None
    players = []
    for line in latest:
        if not line.startswith('PROS = '):
            continue
        for match in re.finditer(r'\bNAME = ("(?:\\.|[^"\\])*").*?\bPID = (\d+).*?\bSTAT = (\d+)', line):
            try:
                name = json.loads(match[1])
            except ValueError:
                name = match[1]
            players.append({'persona_id': int(match[2]), 'display_name': name, 'state': int(match[3])})
    return {'game_id': number(r'\bGID = (\d+)'), 'network_topology': number(r'\bNTOP = (\d+)'),
            'topology_host_persona': number(r'THST \{[^}]*?HPID = (\d+)'),
            'dedicated_host_persona': number(r'DHST \{[^}]*?HPID = (\d+)'),
            'setup_reason_result': number(r'REAS = .*?RSLT = (\d+)'), 'players': players}


def collect(root, pid, get=fetch):
    report = {'schema': 1, 'captured_utc': datetime.now(timezone.utc).isoformat(), 'pid': pid}
    blaze, lines = tail(root / 'data/logs/blaze.log')
    requests = [line for line in lines if line.startswith(('SCNA = ', 'TMPA = '))]
    latest = requests[-1] if requests else None
    parsed = parse_attributes(latest) if latest else None
    blaze['latest_launch_attributes'] = latest[:16000] if latest else None
    blaze['parsed_launch_attributes'] = dict(zip(ATTRIBUTES, parsed)) if parsed is not None else None
    blaze['latest_game_setup'] = latest_setup(lines)
    blaze['recent_game_manager_requests'] = [line[:1000] for line in lines if '<- GameManager ' in line][-30:]
    blaze['recent_user_session_requests'] = [line[:1000] for line in lines if '<- UserSessions ' in line][-30:]
    blaze['recent_selection_updates'] = [line[:1000] for line in lines if line.startswith('ATTR = ')][-20:]
    report['blaze'] = blaze
    mission, mission_lines = tail(root / f'data/active_mission_{pid}.txt')
    mission['tail'] = [line[:2000] for line in mission_lines[-100:]]
    report['mission_hook'] = mission
    debugger, debugger_lines = tail(root / 'data/logs/gdb_active_mission.log')
    debugger['tail'] = [line[:2000] for line in debugger_lines[-60:]]
    report['debugger'] = debugger
    bigs, bigs_lines = tail(root / 'data/logs/bigs.log')
    bigs['recent_http_requests'] = [line[:1000] for line in bigs_lines if re.match(r'^(GET|POST|PUT|DELETE|PATCH) /', line)][-50:]
    report['bigs'] = bigs
    for key, route in (('active_character', '/api/character/1000/characters/active'), ('persistence', '/api/character/1000/persistence')):
        try:
            value = get(route)
            if not isinstance(value, dict):
                raise ValueError('Expected a BIGS JSON object')
            if key == 'persistence':
                elements = json.loads(value['persistenceJsonData'])
                if not isinstance(elements, list):
                    raise ValueError('Expected a persistence array')
                def property_value(definition, prop):
                    return next((p.get('Value') for e in elements if isinstance(e, dict) and e.get('DefinitionId') == definition
                                 for p in e.get('RegisteredDefPropArray', []) if isinstance(p, dict) and p.get('PropertyId') == prop), None)
                value = {'element_count': len(elements), 'freeplay_quest_state': property_value(FREEPLAY_DEFINITION, 1),
                         'freeplay_eligibility_flag': property_value(FREEPLAY_GATE_DEFINITION, FREEPLAY_GATE_PROPERTY)}
            else:
                character = value.get('character', {})
                if not isinstance(character, dict):
                    raise ValueError('Expected a character object')
                value = {'characterId': value.get('characterId'), **{key: character.get(key) for key in ('name', 'gender', 'level', 'xp')}}
            report[key] = value
        except (OSError, ValueError, KeyError, TypeError) as error:
            report[key] = {'error': str(error)}
    points = root / f'data/quest_points_{pid}.json'
    try:
        if points.stat().st_size > 2 << 20:
            raise ValueError('Quest-point report exceeds limit')
        table = json.loads(points.read_text(encoding='utf-8'))
        if not isinstance(table, dict):
            raise ValueError('Expected a quest-point object')
        selected = str(parsed[1]) if parsed is not None else str(FREEPLAY_QUEST)
        report['quest_points'] = {'component_count': len(table.get('composants', [])), 'quest_count': len(table.get('quetes', {})),
                                  'checked_quest': selected, 'point': table.get('quetes', {}).get(selected)}
    except (OSError, ValueError, TypeError) as error:
        report['quest_points'] = {'error': str(error)}
    for name in ('mission_state', 'mission_supervisor'):
        path = root / f'data/{name}_{pid}.json'
        try:
            if path.stat().st_size > 1 << 20:
                raise ValueError('Lifecycle report exceeds limit')
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or str(value.get('pid')) != str(pid):
                raise ValueError('Lifecycle report does not match this PID')
            report[name] = value
        except (OSError, ValueError) as error:
            report[name] = {'error': str(error)}
    lifecycle = report['mission_state']
    run_id, generation = lifecycle.get('run_id'), lifecycle.get('generation')
    if isinstance(run_id, str) and re.fullmatch(r'[a-f0-9]{32}', run_id) and type(generation) is int and generation > 0:
        for name in ('mission', 'mission_prepare'):
            info, lines = tail(root / f'data/logs/{name}_{pid}_{run_id[:8]}_{generation}.log')
            info['tail'] = [line[:2000] for line in lines[-80:]]
            report[name + '_worker'] = info
    lifecycle_events = []
    _, blaze_lines = tail(root / 'data/logs/blaze.log')
    for line in blaze_lines:
        if line.startswith('MISSION_EVENT = '):
            try:
                record = json.loads(line[len('MISSION_EVENT = '):])
                if isinstance(record, dict):
                    lifecycle_events.append(record)
            except ValueError:
                pass
    report['blaze']['recent_lifecycle_events'] = lifecycle_events[-20:]
    # These labels describe evidence in the log tail, not a confirmed cause.
    if lifecycle.get('schema') == 1 and lifecycle.get('phase'):
        stage = 'lifecycle_' + str(lifecycle['phase'])
    elif any('état de démarrage 8' in line for line in mission_lines):
        stage = 'internal_server_started_once'
    elif any('lancement de mission :' in line for line in mission_lines):
        stage = 'connection_hook_reached_no_startup_8_seen'
    elif latest is not None:
        stage = 'launch_attributes_seen_no_connection_hook_seen'
    else:
        stage = 'no_launch_attributes_in_blaze_tail'
    report['observed_stage'] = stage
    report['limitations'] = ['Log tails can include earlier attempts or an earlier process that used the same PID.',
                            'A selected quest or Ready attribute alone does not prove matchmaking was requested.',
                            'This report does not verify running patcher state or read game memory.']
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.pid <= 0:
        parser.error('--pid must be positive')
    output = args.output or Path(f'mission-diagnostics-{args.pid}-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json')
    report = collect(Path(__file__).resolve().parents[2], args.pid)
    with output.open('x', encoding='utf-8') as destination:
        json.dump(report, destination, ensure_ascii=False, indent=2)
    print(f"Observed stage: {report['observed_stage']}")
    print(f'Report: {output.resolve()}')


if __name__ == '__main__':
    main()
