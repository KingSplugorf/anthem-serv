"""Native repeatable mission hooks: scoped launch records, exit and recovery.

Only the supported build's existing validated hooks are used. No return-to-hub
function is called or invented; the game's existing End Expedition flow remains
responsible for the transfer. This module restores the default hub launch point.
"""
import json
import os
from pathlib import Path
import struct
import time

from mission_hooks import MissionHooks, MISSION_DEBUGGER_READY
from mission_lease import Lease
from end_expedition import STORY_SUCCESS_CALLBACK, STORY_SUCCESS_CODE
import hub_game

LEAVE_SUCCESS = 0x1481F14D2
LEAVE_CODE = bytes.fromhex('b2014889f9ff5718')
PLAYER_SLOTS = 0x142B2ABE0
EVENT_PREFIX = b'MISSION_EVENT = '


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(10):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.01)


class EventLog:
    def __init__(self, path):
        self.path, self.buffer = Path(path), b''
        try:
            stat = self.path.stat()
            self.position, self.identity = stat.st_size, (stat.st_dev, stat.st_ino)
        except FileNotFoundError:
            self.position, self.identity = 0, None

    def poll(self):
        result = []
        try:
            with self.path.open('rb') as source:
                stat = os.fstat(source.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if (self.identity is not None and identity != self.identity) or stat.st_size < self.position:
                    result.append({'event': 'server_reset'})
                    self.position, self.buffer = 0, b''
                self.identity = identity
                source.seek(self.position)
                data = self.buffer + source.read(4 << 20)
                self.position = source.tell()
        except FileNotFoundError:
            return result
        lines = data.split(b'\n')
        self.buffer = lines.pop()[-65536:]
        for line in lines:
            if line.startswith(EVENT_PREFIX) and len(line) < 65536:
                try:
                    value = json.loads(line[len(EVENT_PREFIX):])
                    if isinstance(value, dict):
                        result.append(value)
                except (ValueError, UnicodeError):
                    continue
        return result


class TransitionPoints:
    def __init__(self, memory, path):
        self.memory, self.path, self.changed = memory, Path(path), []

    def text(self, address):
        return self.memory.read(address, 64).split(b'\0')[0].decode('latin1')

    def apply(self, quest):
        table = json.loads(self.path.read_text(encoding='utf-8'))
        point = table.get('quetes', {}).get(str(quest))
        originals = table.get('originaux', [])
        if not point or not originals or len(originals) != len(table.get('composants', [])):
            raise OSError('Quest launch-point metadata is missing; wait for preparation before retrying')
        if self.text(point['chaine']) != point['point']:
            raise OSError('Quest launch-point string moved; launch-point scan must be refreshed')
        writes = []
        for entry in originals:
            component = entry['component']
            original = struct.pack('<QQ', *entry['pointers'])
            if (struct.unpack('<Q', self.memory.read(component, 8))[0] != entry['identity']
                    or self.memory.read(component + 0x30, 16) != original
                    or struct.unpack('<I', self.memory.read(component + 0x40, 4))[0] != 1
                    or [self.text(pointer) for pointer in entry['pointers']] != entry['names']
                    or any(name != 'Enclave_HQ' for name in entry['names'])):
                raise OSError('Root transition component changed; launch-point scan must be refreshed')
            writes.append((entry, original, struct.pack('<QQ', point['chaine'], point['chaine'])))
        try:
            for entry, original, patched in writes:
                self.changed.append((entry, original, patched))
                self.memory.write(entry['component'] + 0x30, patched)
        except BaseException:
            self.restore()
            raise
        return point['point']

    def restore(self):
        restored, skipped = 0, 0
        for entry, original, patched in self.changed:
            component = entry['component']
            try:
                if (self.memory.read(component + 0x30, 16) == patched
                        and struct.unpack('<Q', self.memory.read(component, 8))[0] == entry['identity']
                        and [self.text(pointer) for pointer in entry['pointers']] == entry['names']):
                    self.memory.write(component + 0x30, original)
                    restored += 1
                else:
                    skipped += 1
            except OSError:
                skipped += 1
        self.changed.clear()
        return restored, skipped


class RepeatingMissionHooks(MissionHooks):
    def __init__(self, memory, pid, run_id, data=Path('data'), persona=1000, clock=time.monotonic, timeout=180):
        super().__init__(memory, pid, data)
        self.run_id, self.persona, self.clock, self.timeout = run_id, persona, clock, timeout
        self.generation, self.sequence = 0, 0
        self.phase, self.ready = 'attaching', False
        self.pending = self.current = self.last = None
        self.queued_events, self.seen_requests = [], set()
        self.deadline, self.last_startup_state = None, None
        self.points = None
        self.report_path = self.data / f'mission_state_{pid}.json'
        self.lease_path = self.data / f'mission_lease_{pid}_{run_id}.bin'
        self.lease = Lease(self.lease_path, run_id, create=True)
        self.events = EventLog(self.data / 'logs/blaze.log')
        try:
            self.publish('Debugger attaching')
        except BaseException:
            self.lease.close()
            raise

    def publish(self, reason):
        self.sequence += 1
        atomic_json(self.report_path, {'schema': 1, 'run_id': self.run_id, 'pid': self.pid,
            'persona_id': self.persona, 'sequence': self.sequence, 'generation': self.generation,
            'phase': self.phase, 'ready': self.ready, 'reason': reason,
            'mission': self.current or self.pending or self.last,
            'lease': str(self.lease_path.resolve()), 'updated_at': time.time()})
        self.log(f'lifecycle {self.phase} #{self.generation}: {reason}')

    def mark_ready(self):
        self.ready = True
        if self.phase == 'attaching':
            self.phase = 'idle'
        self.publish('Waiting for an expedition')
        self.log(MISSION_DEBUGGER_READY)

    def clear_player(self):
        if self.generation == 0:
            return
        try:
            values = struct.unpack('<QQQ', self.memory.read(PLAYER_SLOTS, 24))
            if any(values) and all(value == 0 or 0x10000 <= value < 1 << 47 for value in values):
                self.memory.write(PLAYER_SLOTS, bytes(24))
            # Clear the owned destructible-health queue before its client hook
            # can encounter entries from the previous level.
            site, target = 0x14096B9A0, 0x142B29800
            if self.memory.read(site, 5) == b'\xe9' + struct.pack('<i', target - site - 5):
                self.memory.write(0x153737F00, bytes(8 * 16))
        except OSError:
            pass

    def finish(self, phase, reason):
        self.lease.deactivate()
        self.clear_player()
        self.launched = None
        if self.points is not None:
            restored, skipped = self.points.restore()
            self.log(f'points de retour: {restored} restaurés, {skipped} invalides laissés intacts')
            self.points = None
        self.last = self.current or self.pending or self.last
        self.pending = self.current = None
        self.deadline, self.phase = None, phase
        self.publish(reason)

    @staticmethod
    def key(record):
        return record.get('server_run_id'), record.get('game_id')

    def poll(self):
        records, self.queued_events = self.queued_events + self.events.poll(), []
        for record in records:
            if record.get('event') == 'server_reset':
                self.finish('idle', 'Server log restarted; stale expedition discarded')
                continue
            if record.get('schema') != 1 or record.get('persona_id') != self.persona:
                continue
            kind = record.get('event')
            attributes = record.get('attributes')
            if kind == 'requested':
                if (not isinstance(record.get('server_run_id'), str) or type(record.get('game_id')) is not int
                        or not isinstance(attributes, list) or len(attributes) != 4
                        or any(type(value) is not int or not 0 <= value <= 0xffffffff for value in attributes)):
                    continue
                if self.key(record) in self.seen_requests:
                    continue
                self.seen_requests.add(self.key(record))
                if self.current and self.key(record) == self.key(self.current):
                    continue
                if self.current or self.pending:
                    self.finish('idle', 'Previous expedition superseded')
                self.pending, self.phase = record, 'queued'
                self.deadline = self.clock() + self.timeout
                self.publish('Server accepted a new expedition')
            elif kind in ('left', 'cancelled', 'superseded', 'disconnected'):
                if any(item and self.key(item) == self.key(record) for item in (self.current, self.pending)):
                    self.finish('idle', f'Server expedition {kind}')
        if self.deadline is not None and self.clock() >= self.deadline:
            self.finish('failed', 'Expedition startup timed out; hooks reset for a fresh request')

    def has_updates(self):
        # This read-only check runs while the process executes. Applying events
        # and restoring pointers happens only under a debug event or suspension.
        self.queued_events.extend(self.events.poll())
        return bool(self.queued_events) or (self.deadline is not None and self.clock() >= self.deadline)

    def connect(self, registers):
        self.poll()
        if registers['rdx'] & 0xffffffff != 5:
            if self.current and self.phase == 'starting':
                self.finish('idle', 'Connection changed to a remote join')
            return
        try:
            game = self.qword(registers['rsi'] + 0x50)
            if hub_game.is_hub_game(game):
                self.finish('idle', 'Shared hub connection')
                return
            if not self.pending or self.pending['game_id'] != game:
                self.log(f'lifecycle: partie {game} sans nouvelle demande correspondante; aucun ancien lancement rejoué')
                return
            self.current, self.pending = self.pending, None
            self.generation += 1
            self.launched = tuple(self.current['attributes'])
            self.last_startup_state = None
            self.phase = 'starting'
            self.deadline = self.clock() + self.timeout
            pointer, _ = self.starting_info()
            if pointer < 0x10000 or pointer % 4:
                raise OSError('GameStartingInfo not ready for this expedition')
            if self.memory.read(LEAVE_SUCCESS, len(LEAVE_CODE)) != LEAVE_CODE:
                raise OSError('End Expedition callback does not match the supported build')
            if self.memory.read(STORY_SUCCESS_CALLBACK, len(STORY_SUCCESS_CODE)) != STORY_SUCCESS_CODE:
                raise OSError('Story expedition callback does not match the supported build')
            self.points = TransitionPoints(self.memory, self.data / f'quest_points_{self.pid}.json')
            point = self.points.apply(self.launched[1])
            self.use_root_level(registers['rcx'])
            self.memory.write(pointer, struct.pack('<IIII', *self.launched))
            self.publish(f'Preparing internal server at {point}')
        except (OSError, ValueError, KeyError, struct.error) as error:
            self.finish('failed', f'Launch rejected safely: {error}')

    def startup(self, registers):
        self.poll()
        if self.launched is None:
            return
        try:
            state = registers['rdx'] & 0xffffffff
            if state != self.last_startup_state:
                super().startup(registers)
                self.last_startup_state = state
            else:
                startup = registers['rcx']
                self.memory.write(startup + 0x18, struct.pack('<IIII', *self.launched))
                self.memory.write(startup + 0x31, b'\x01')
            if state == 8:
                self.launched, self.deadline, self.phase = None, None, 'active'
                self.lease.activate(self.generation)
                self.publish('Internal mission server started; startup overrides released')
        except (OSError, ValueError, struct.error) as error:
            self.finish('failed', f'Startup rejected safely: {error}')

    def gate(self, registers):
        self.poll()
        if self.launched is not None:
            try:
                super().gate(registers)
            except OSError as error:
                self.finish('failed', f'Startup gate became invalid: {error}')

    def playmode_write(self, registers):
        self.gate(registers)

    def leave(self, registers):
        if self.phase == 'active':
            self.finish('returning', 'Expedition leave accepted; hub default restored')

    def close(self, process_exited=False):
        try:
            if process_exited:
                self.lease.deactivate()
                self.launched, self.phase, self.ready = None, 'stopped', False
                self.publish('Game process exited')
            else:
                self.ready = False
                self.finish('stopped', 'Mission debugger stopped')
        finally:
            self.lease.close()
