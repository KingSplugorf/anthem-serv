"""Keep the native mission debugger and expedition-scoped workers supervised."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from mission_lease import Lease
from mission_lifecycle import atomic_json


def stop_tree(child):
    if child is None or getattr(child, '_mission_stopped', False) is True:
        return
    if child.poll() is None and child.stdin is not None:
        try:
            child.stdin.write(b'stop\n')
            child.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
    job = getattr(child, 'job', None)
    if job is not None:
        job.close()  # Includes descendants even when their worker already exited.
        child.job = None
    elif child.poll() is None:
        if os.name == 'nt':
            result = subprocess.run(['taskkill.exe', '/PID', str(child.pid), '/T', '/F'],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
            if result.returncode and child.poll() is None:
                raise OSError(f'Could not stop owned expedition process {child.pid}')
        else:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    if os.name != 'nt':
        # The PID is also our owned session/group ID; descendants keep it alive.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            os.killpg(child.pid, signal.SIGKILL)
        else:
            child.kill()
        child.wait(timeout=3)
    if child.stdin is not None:
        child.stdin.close()
    if os.name != 'nt':
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    child._mission_stopped = True


def stop_debugger(child):
    if child.poll() is None:
        # Never force-kill a live game's debugger: allow register restoration.
        # On failure the launcher stops the game before its owned process tree.
        child.communicate(input=b'stop\n', timeout=15)
    elif child.stdin is not None:
        child.stdin.close()
    if child.returncode not in (None, 0):
        raise RuntimeError(f'Mission debugger did not detach cleanly ({child.returncode})')


class Supervisor:
    def __init__(self, pid, run_id, debugger, launch_worker, launch_prepare, alive,
                 data=Path('data'), clock=time.monotonic, stop=stop_tree, debugger_stop=stop_debugger, announce=print,
                 status_callback=lambda _: None, prepare_initial=False):
        self.pid, self.run_id, self.debugger = pid, run_id, debugger
        self.launch_worker, self.launch_prepare, self.alive = launch_worker, launch_prepare, alive
        self.data, self.clock, self.stop, self.announce = Path(data), clock, stop, announce
        self.debugger_stop = debugger_stop
        self.status_callback = status_callback
        self.worker = self.preparer = None
        self.worker_generation, self.handled_generation, self.prepared_generation = 0, 0, 0
        self.prepare_initial = prepare_initial
        self.prepare_generation = None
        if prepare_initial: self.prepared_generation = -1
        self.prepare_deadline, self.last_status = None, None
        self.last_phase, self.worker_error, self.prepare_error = None, None, None
        self.lease = Lease(self.data / f'mission_lease_{pid}_{run_id}.bin', run_id)

    def cancel_worker(self):
        if self.worker is not None:
            self.lease.deactivate(self.worker_generation)
            self.stop(self.worker)
            self.worker = None

    def step(self):
        if self.debugger.poll() is not None:
            # The game can exit between run()'s liveness check and this poll.
            # A successful debugger exit is normal only with game-exit evidence.
            if self.debugger.returncode == 0:
                if not self.alive(self.pid):
                    return False
                try:
                    final = json.loads((self.data / f'mission_state_{self.pid}.json').read_text(encoding='utf-8'))
                    if (final.get('run_id') == self.run_id and str(final.get('pid')) == str(self.pid)
                            and final.get('phase') == 'stopped' and final.get('reason') == 'Game process exited'):
                        return False
                except (OSError, ValueError):
                    pass
            raise RuntimeError(f'Mission debugger exited ({self.debugger.returncode}); inspect data/logs/gdb_active_mission.log')
        try:
            state = json.loads((self.data / f'mission_state_{self.pid}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        if state.get('run_id') != self.run_id:
            return
        phase, generation = state['phase'], state['generation']
        native_intro = (state.get('mission') or {}).get('origin', '').startswith('native-story-')
        if phase != self.last_phase:
            self.announce(f"Expedition {generation}: {phase} — {state.get('reason', '')}")
            self.last_phase = phase
        if self.worker is not None and (phase != 'active' or generation != self.worker_generation):
            self.cancel_worker()
        if phase in ('queued', 'starting', 'active') and not native_intro:
            self.stop(self.preparer)
            self.preparer = None
        if phase == 'active' and generation > self.handled_generation:
            self.cancel_worker()
            self.worker_generation = self.handled_generation = generation
            self.worker_error = None
            if self.lease.active(generation):
                self.worker = self.launch_worker(generation, self.lease.path)
        if self.worker is not None and self.worker.poll() is not None:
            if self.lease.active(generation):
                self.worker_error = f'Expedition worker exited ({self.worker.returncode}); see its per-expedition log'
                self.announce(self.worker_error)
            self.stop(self.worker)
            self.worker = None
        preparation_phase = phase in ('returning', 'idle', 'failed') or (
            self.prepare_initial and self.prepared_generation < 0 and native_intro)
        if preparation_phase and generation > self.prepared_generation:
            if self.preparer is None:
                target = 0 if self.prepare_initial and self.prepared_generation < 0 else generation
                self.preparer = self.launch_prepare(target)
                self.prepare_generation = target
                self.prepare_deadline = self.clock() + 120
                self.prepare_error = None
            elif self.preparer.poll() is not None:
                code = self.preparer.returncode
                self.stop(self.preparer)
                self.preparer = None
                self.prepared_generation = self.prepare_generation
                if code == 0:
                    self.announce('Launch points refreshed; ready for another expedition')
                else:
                    self.prepare_error = f'Launch-point refresh failed ({code}); inspect its preparation log'
                    self.announce(self.prepare_error)
            elif self.clock() >= self.prepare_deadline:
                self.stop(self.preparer)
                self.preparer = None
                self.prepared_generation = self.prepare_generation
                self.prepare_error = 'Launch-point refresh timed out; inspect its preparation log'
                self.announce(self.prepare_error)
        status = {'schema': 1, 'run_id': self.run_id, 'pid': self.pid, 'phase': phase, 'generation': generation,
                  'reason': state.get('reason', ''),
                  'worker_pid': self.worker.pid if self.worker else None,
                  'prepare_pid': self.preparer.pid if self.preparer else None,
                  'worker_error': self.worker_error, 'prepare_error': self.prepare_error,
                  'prepared_generation': self.prepared_generation,
                  'ready_for_launch': phase in ('idle', 'returning', 'failed') and self.preparer is None
                      and self.prepared_generation >= generation and self.prepare_error is None}
        if status != self.last_status:
            atomic_json(self.data / f'mission_supervisor_{self.pid}.json', status)
            self.last_status = status
            self.status_callback(status)

    def run(self):
        try:
            while self.alive(self.pid):
                if self.step() is False:
                    break
                time.sleep(0.1)
        finally:
            self.close()

    def close(self):
        try:
            self.cancel_worker()
            self.stop(self.preparer)
            self.preparer = None
            self.debugger_stop(self.debugger)
        finally:
            self.lease.close()
