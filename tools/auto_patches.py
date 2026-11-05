"""Usage: python3 -I tools/auto_patches.py PID.

Prepare the supported game's mission hooks and combat patches. Native Windows
keeps its debugger attached across expeditions: each accepted Blaze request gets
fresh startup state, a scoped worker tree, and a write lease revoked on exit,
cancellation or timeout. Return restores the hub launch point and refreshes the
next mission's metadata. ANTHEM_MISSION_LIFECYCLE=0 selects the previous one-shot
Windows behavior. Linux/GDB and peer-host paths retain their existing flow.

data/combat_patches.txt optionally restricts combat patches (one relative script
per line; suivi enables health/position followers). Internal --mission-worker
and --prepare-mission children must receive their supervisor's stdin start gate.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from process_memory import open_memory, alive as process_alive
from mission_hooks import MISSION_DEBUGGER_READY

import os
import json
import re
import subprocess
import time
import threading
import uuid
import argparse
from concurrent.futures import ThreadPoolExecutor

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(TOOLS, "combat_patches", "health"))
sys.path.insert(0, os.path.join(TOOLS, "combat_patches", "ai"))
from health_mirror import Process

STARTING_INFO_POINTER = 0x144A92578
MISSION_STARTED = "état de démarrage 8"
COMBAT = os.path.join(TOOLS, "combat_patches")
NAVIGATION = os.path.join("combat_patches", "ai", "navigation.py")
MISSION_GROUPS = ((("health/creations.py", ()), ("health/death.py", ()), ("server_behaviours.py", ()), ("health/published_health.py", ()), ("health/damage.py", ()), ("health/reactions.py", ())),
                  (("server_damage.py", ()), ("health/server_health.py", ()), ("health/server_sections.py", ())),
                  (("ai/target.py", ("0",)), ("ai/shots.py", ()), ("ai/movements.py", ()), ("ai/perception.py", ())))
COMBAT_PATCHES = sum(MISSION_GROUPS, ())
HEALTH_FOLLOW_SECONDS = 4 * 3600
COMBAT_CHOICE = os.path.join("data", "combat_patches.txt")
HEALTH_FOLLOW, EXIT_PROBE = "suivi", "sonde_sortie"
LEGACY_PATCH_NAMES = {
    "comportements_serveur.py": "server_behaviours.py",
    "degats_serveur.py": "server_damage.py",
    "vie/vie_serveur.py": "health/server_health.py",
    "vie/sections_serveur.py": "health/server_sections.py",
    "vie/creations.py": "health/creations.py",
    "vie/mort.py": "health/death.py",
    "vie/sante_publiee.py": "health/published_health.py",
    "vie/degats.py": "health/damage.py",
    "vie/reactions.py": "health/reactions.py",
    "ia/cible.py": "ai/target.py",
    "ia/tirs.py": "ai/shots.py",
    "ia/deplacements.py": "ai/movements.py",
    "ia/perception.py": "ai/perception.py",
}
PEER_HOST = "MISSION_PAIR"
CODE_RETRY_SECONDS, DATA_RETRY_SECONDS = 1, 20
MISSION_POLL_SECONDS, MISSION_RETRY_SECONDS, MISSION_RETRY_LIMIT = 0.2, 2, 600


def alive(pid):
    return process_alive(pid)


def run(tool, *arguments):
    result = subprocess.run([sys.executable, "-X", "utf8", "-I", os.path.join(TOOLS, tool), *arguments], capture_output=True, text=True, encoding="utf-8")
    print(f"{time.strftime('%H:%M:%S')} {tool} : {(result.stdout + result.stderr).strip()}", flush=True)
    return result


def until(pid, delay, attempt):
    while alive(pid):
        if attempt():
            return True
        time.sleep(delay)
    return False


def mission_points_found(report):
    return sum(int(count) for count in re.findall(r": (\d+)", report)) > 0


def starting_info_ready(pid):
    try:
        with open_memory(pid, "rb") as memory:
            memory.seek(STARTING_INFO_POINTER)
            return int.from_bytes(memory.read(8), "little") != 0
    except OSError:
        return False


def mission_log(pid):
    return os.path.join("data", f"active_mission_{pid}.txt")


def wait_for_tls_diagnostics(pid, debugger, timeout=30, run_id=None):
    deadline = time.monotonic() + timeout
    report = os.path.join("data", f"tls_client_{pid}.json")
    while alive(pid):
        if debugger.poll() is not None:
            raise RuntimeError("TLS debugger exited; inspect data/logs/gdb_active_mission.log")
        try:
            with open(report, encoding="utf-8") as source:
                state = json.load(source)
                if state.get("ready") and state.get("run_id") == run_id:
                    return
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError(f"TLS diagnostic breakpoint did not become ready within {timeout:g} seconds")
        time.sleep(0.1)
    raise RuntimeError("Game exited before TLS diagnostics became ready")


def wait_for_local_ca(pid, debugger, run_id, timeout=120):
    deadline, announced = time.monotonic()+timeout, False
    report = os.path.join('data', f'local_ca_{pid}.json')
    while alive(pid):
        # Observe exit before reading: a report read just before child exit can
        # precede its final detached=true write even when poll() then sees 0.
        result = debugger.poll()
        state = {}
        try:
            with open(report, encoding='utf-8') as source:
                candidate = json.load(source)
                if candidate.get('run_id') == run_id: state = candidate
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            pass
        if state.get('ready') and not announced:
            print('Win32 local CA loader ready: retry connecting in the game if needed', flush=True)
            announced = True
        if result is not None:
            if (result == 0 and state.get('loaded') and state.get('completed') and state.get('detached')
                    and type(state.get('native_result')) is int and state['native_result'] > 0):
                print(f"Win32 local CA registered: native result {state['native_result']}; continuing mission setup", flush=True)
                return True
            raise RuntimeError(f'Local CA registration did not succeed; inspect {report} and data/logs/windows_ca_loader.log')
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Local CA registration did not finish within {timeout:g} seconds; inspect {report} and data/logs/windows_ca_loader.log')
        time.sleep(0.1)
    return False


def load_local_ca(pid):
    run_id = uuid.uuid4().hex
    certificate = os.path.join(os.path.dirname(TOOLS), 'data', 'certs', 'test.crt')
    command = [sys.executable, '-X', 'utf8', '-I', os.path.join(TOOLS, 'windows_debugger.py'),
               pid, '--ca-certificate', certificate]
    with open(os.path.join('data', 'logs', 'windows_ca_loader.log'), 'w', encoding='utf-8') as log:
        debugger = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, env={**os.environ, 'ANTHEM_CA_RUN_ID': run_id})
        try:
            return wait_for_local_ca(pid, debugger, run_id)
        finally:
            if debugger.poll() is None:
                # A self-contained call stub can finish after graceful detach;
                # do not force-kill a debugger while its game thread is calling.
                debugger.communicate(input=b'stop\n', timeout=15)
            elif debugger.stdin is not None:
                debugger.stdin.close()


def wait_for_mission_start(pid, start, debugger=None):
    while alive(pid):
        if debugger is not None and debugger.poll() is not None:
            raise RuntimeError(f"Mission debugger exited ({debugger.returncode}); inspect data/logs/gdb_active_mission.log")
        if os.path.exists(mission_log(pid)):
            with open(mission_log(pid), errors="replace", encoding="utf-8") as log:
                log.seek(start)
                if MISSION_STARTED in log.read():
                    return True
        time.sleep(MISSION_POLL_SECONDS)
    return False


def wait_for_mission_debugger(pid, start, debugger, timeout=30):
    """Only report the native debugger ready after its initial debug event."""
    deadline = time.monotonic() + timeout
    while alive(pid):
        if debugger.poll() is not None:
            raise RuntimeError(f"Mission debugger exited ({debugger.returncode}) before readiness; inspect data/logs/gdb_active_mission.log")
        try:
            with open(mission_log(pid), encoding="utf-8", errors="replace") as log:
                log.seek(start)
                if MISSION_DEBUGGER_READY in log.read():
                    return True
        except FileNotFoundError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Native mission debugger did not become ready within {timeout:g} seconds; inspect data/logs/gdb_active_mission.log")
        time.sleep(0.1)
    return False


def combat_choice():
    if not os.path.exists(COMBAT_CHOICE):
        return [script for script, _ in COMBAT_PATCHES] + [HEALTH_FOLLOW]
    with open(COMBAT_CHOICE, encoding="utf-8-sig") as choice:
        entries = [line.strip().replace("\\", "/") for line in choice if line.strip() and not line.lstrip().startswith("#")]
    selected = list(dict.fromkeys(LEGACY_PATCH_NAMES.get(name, name) for name in entries))
    supported = {script for script, _ in COMBAT_PATCHES} | {HEALTH_FOLLOW, EXIT_PROBE, "ai/player_position.py"}
    unknown = set(selected) - supported
    if unknown:
        raise ValueError(f"Unknown combat patches in {COMBAT_CHOICE}: {', '.join(sorted(unknown))}")
    return selected


def apply_in_order(pid, patches, cancelled=lambda: False, children=None):
    for index, (script, arguments) in enumerate(patches):
        if cancelled():
            return ()
        command = [sys.executable, "-X", "utf8", "-I", os.path.join(COMBAT, script), pid, "poser", *arguments]
        if children is not None:
            command.insert(4, os.path.join(TOOLS, 'mission_child.py'))
        result = (children.run(command, text=True, encoding='utf-8') if children is not None else
                  subprocess.run(command, capture_output=True, text=True, encoding="utf-8"))
        last_line = ((result.stdout + result.stderr).strip().splitlines() or [""])[-1]
        if result.returncode and children is not None:
            last_line = f'exit {result.returncode}\n' + (result.stdout + result.stderr).strip()[-16384:]
        print(f"{time.strftime('%H:%M:%S')} {script} : {last_line}", flush=True)
        if result.returncode:
            return patches[index:]
    return ()


def follow(pid, chosen, children=None):
    spawn = children.start if children is not None else subprocess.Popen
    if "ai/player_position.py" in chosen or HEALTH_FOLLOW in chosen:
        with open(os.path.join("data", "logs", "player_position.log"), "a") as log:
            spawn([sys.executable, "-X", "utf8", "-I", os.path.join(COMBAT, "ai", "player_position.py"), pid, "--duree", str(HEALTH_FOLLOW_SECONDS)],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=children is None)
        print(f"{time.strftime('%H:%M:%S')} player_position.py : lancé", flush=True)
    if HEALTH_FOLLOW not in chosen:
        return
    with open(os.path.join("data", "logs", "health_follow.log"), "a") as log:
        spawn([sys.executable, "-X", "utf8", "-I", os.path.join(COMBAT, "health", "health_follow.py"), pid, "--duree", str(HEALTH_FOLLOW_SECONDS)],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=children is None)
    print(f"{time.strftime('%H:%M:%S')} health_follow.py : lancé", flush=True)


def find_player(process):
    import player
    character, health, shield = player.find(process)
    if character:
        player.write_slots(process, character, health, shield)
    print(f"{time.strftime('%H:%M:%S')} player.py : player {character:#x}, copies clientes vie {health:#x}, bouclier {shield:#x}"
          f"{'' if character else ' (laissé à player_position.py)'}", flush=True)


def apply_mission_patches(pid, chosen, cancelled=lambda: False, children=None):
    if not chosen or cancelled():
        return True
    process = Process(int(pid))
    groups = [tuple(patch for patch in group if patch[0] in chosen) for group in MISSION_GROUPS]
    player_pending = True
    deadline = time.monotonic() + MISSION_RETRY_LIMIT
    try:
        with ThreadPoolExecutor(len(groups) + 1) as pool:
            while alive(pid) and not cancelled():
                search = pool.submit(find_player, process) if player_pending else None
                groups = list(pool.map(lambda group: apply_in_order(pid, group, cancelled, children), groups))
                if search:
                    try:
                        search.result()
                    except OSError as error:
                        # Level objects can disappear during the initial scan.
                        # Keep the worker and patch retries alive until they are
                        # available; a partial setup must not count as ready.
                        print(f"{time.strftime('%H:%M:%S')} player scan not ready: {error}; retrying", flush=True)
                    else:
                        player_pending = False
                        if not cancelled():
                            follow(pid, chosen, children)
                if cancelled():
                    return False
                if not any(groups) and not player_pending:
                    print(f"{time.strftime('%H:%M:%S')} correctifs de mission posés", flush=True)
                    return True
                if time.monotonic() >= deadline:
                    raise RuntimeError("Mission patches did not finish before the retry deadline")
                for _ in range(20):
                    if cancelled():
                        return False
                    time.sleep(MISSION_RETRY_SECONDS / 20)
    finally:
        process.memory.close()


def child_stop_event():
    stop = threading.Event()
    def listen():
        for line in sys.stdin:
            if line.strip() == 'stop':
                break
        stop.set()
    threading.Thread(target=listen, daemon=True).start()
    return stop


def mission_worker(pid, run_id, generation):
    from mission_lease import Lease, MissionInactive
    from mission_workers import Children
    stop = child_stop_event()
    with Lease(os.environ['ANTHEM_MISSION_LEASE'], run_id) as lease:
        cancelled = lambda: stop.is_set() or not lease.active(generation)
        with Children(cancelled) as children:
            try:
                if apply_mission_patches(pid, combat_choice(), cancelled, children):
                    while alive(pid) and not cancelled():
                        stop.wait(0.1)
            except (MissionInactive, InterruptedError):
                if not cancelled():
                    raise


def prepare_mission(pid):
    from mission_workers import Children
    stop = child_stop_event()
    with Children(stop.is_set) as children:
        for script, arguments in [('missions_inproc.py', ()), ('default_point.py', ('--quetes',))]:
            while alive(pid) and not stop.is_set():
                command = [sys.executable, '-X', 'utf8', '-I', os.path.join(TOOLS, script), pid, *arguments]
                result = children.run(command, text=True, encoding='utf-8')
                print((result.stdout + result.stderr).strip(), flush=True)
                if (mission_points_found(result.stdout) if script == 'missions_inproc.py' else result.returncode == 0):
                    break
                stop.wait(DATA_RETRY_SECONDS)
            else:
                return


def supervise_missions(pid, run_id, debugger):
    from mission_jobs import managed_spawn
    from mission_supervisor import Supervisor
    def launch(generation, lease=None):
        name = 'mission' if lease is not None else 'mission_prepare'
        env = dict(os.environ)
        command = [sys.executable, '-X', 'utf8', '-I', os.path.join(TOOLS, 'auto_patches.py'), pid]
        if lease is not None:
            env.update(ANTHEM_MISSION_LEASE=str(lease.resolve()), ANTHEM_MISSION_RUN_ID=run_id,
                       ANTHEM_MISSION_GENERATION=str(generation))
            command += ['--mission-worker', str(generation), '--mission-session', run_id]
        else:
            command += ['--prepare-mission']
        path = os.path.join('data', 'logs', f'{name}_{pid}_{run_id[:8]}_{generation}.log')
        with open(path, 'w', encoding='utf-8') as output:
            return managed_spawn(command, env=env, stdout=output, stderr=subprocess.STDOUT)
    Supervisor(pid, run_id, debugger, launch, lambda generation: launch(generation), alive,
               announce=lambda text: print(f"{time.strftime('%H:%M:%S')} {text}", flush=True),
               status_callback=lambda state: print('MISSION_STATUS = ' + json.dumps(state), flush=True),
               prepare_initial=os.environ.get('ANTHEM_STORY_MODE') == '1').run()


def launch_steps(environment):
    redirection = ("launch_from_hub.py", "--pair") if environment.get(PEER_HOST) == "1" else ("launch_from_hub.py",)
    steps = [redirection, (NAVIGATION, "poser"), ("end_expedition.py", "poser")]
    # Attach the story startup observer before the large heap scans. The
    # supervisor owns background preparation alongside the native introduction.
    if environment.get('ANTHEM_STORY_MODE') != '1':
        steps += [("missions_inproc.py",), ("default_point.py", "--quetes")]
    return steps


def step_done(pid, step):
    result = run(step[0], pid, *step[1:])
    return mission_points_found(result.stdout) if step[0] == "missions_inproc.py" else result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description='Game patch startup and native expedition supervision')
    parser.add_argument('pid')
    parser.add_argument('--mission-worker', type=int)
    parser.add_argument('--mission-session')
    parser.add_argument('--prepare-mission', action='store_true')
    args = parser.parse_args()
    pid = args.pid
    if args.mission_worker is not None or args.prepare_mission:
        if sys.stdin.readline().strip() != 'start':
            raise RuntimeError('Managed mission child did not receive its start gate')
        if args.prepare_mission:
            prepare_mission(pid)
        else:
            mission_worker(pid, args.mission_session, args.mission_worker)
        return
    os.makedirs(os.path.join("data", "logs"), exist_ok=True)
    lifecycle = os.name == 'nt' and os.environ.get('ANTHEM_MISSION_LIFECYCLE', '1') == '1' and os.environ.get('ANTHEM_TLS_DIAGNOSTICS') != '1'
    if lifecycle:
        with open(os.path.join('data', 'logs', 'blaze.log'), encoding='utf-8', errors='replace') as source:
            if 'BLAZE_LOCAL_LIFECYCLE_READY = 1' not in source.read(65536):
                raise RuntimeError('Blaze lacks local mission lifecycle support. Rebuild the services or set ANTHEM_MISSION_LIFECYCLE=0 for the previous single-expedition mode.')
    if os.name == "nt":
        # Reject unknown image relocation before any fixed-address patch is attempted.
        from windows_memory import modules
        base, image = next(modules(int(pid)))
        if base != 0x140000000 or Path(image).name.lower() not in ("anthem.exe", "dylan.main_win64_retail.exe"):
            sys.exit("Unsupported game image/base for fixed-address patches")
        if EXIT_PROBE in combat_choice():
            sys.exit("sonde_sortie still requires Linux GDB; remove it from data/combat_patches.txt on Windows")
        if os.environ.get('ANTHEM_LOAD_LOCAL_CA') == '1':
            if os.environ.get('ANTHEM_TLS_DIAGNOSTICS') == '1':
                sys.exit('Remove ANTHEM_TLS_DIAGNOSTICS before enabling local CA registration')
            if not load_local_ca(pid):
                return
        threading.Thread(target=lambda: until(pid, CODE_RETRY_SECONDS,
            lambda: run("patch_finrachaos.py", pid, "--appliquer").returncode == 0), daemon=True).start()
    for step in launch_steps(os.environ):
        retry = DATA_RETRY_SECONDS if step[0] in ("missions_inproc.py", "default_point.py") else CODE_RETRY_SECONDS
        if not until(pid, retry, lambda: step_done(pid, step)):
            return
    if not lifecycle and not until(pid, CODE_RETRY_SECONDS, lambda: starting_info_ready(pid)):
        return
    mission_log_start = os.path.getsize(mission_log(pid)) if os.path.exists(mission_log(pid)) else 0
    native = os.name == "nt"
    tls_diagnostics = native and os.environ.get("ANTHEM_TLS_DIAGNOSTICS") == "1"
    debugger = ([sys.executable, "-X", "utf8", "-I", os.path.join(TOOLS, "windows_debugger.py"), pid]
                if native else ["gdb", "-p", pid, "-batch", "-x", os.path.join(TOOLS, "gdb_active_mission.gdb")])
    run_id = uuid.uuid4().hex if tls_diagnostics else None
    debugger_env = {**os.environ, "ANTHEM_TLS_RUN_ID": run_id} if tls_diagnostics else None
    mission_run_id = uuid.uuid4().hex if lifecycle else None
    if lifecycle:
        debugger += ['--mission-session', mission_run_id]
    with open(os.path.join("data", "logs", "gdb_active_mission.log"), "a") as log:
        mission_debugger = subprocess.Popen(debugger, stdin=subprocess.PIPE if native else subprocess.DEVNULL,
                                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=debugger_env)
    if tls_diagnostics:
        wait_for_tls_diagnostics(pid, mission_debugger, run_id=run_id)
        print("Win32 TLS diagnostics ready: retry the connection in the game; mission hooks are inactive", flush=True)
        while alive(pid):
            if mission_debugger.poll() is not None:
                raise RuntimeError("TLS debugger exited; inspect data/logs/gdb_active_mission.log")
            time.sleep(1)
        return
    if native and not wait_for_mission_debugger(pid, mission_log_start, mission_debugger):
        return
    print(f"{time.strftime('%H:%M:%S')} {'Win32' if native else 'GDB'} mission debugger : posé", flush=True)
    if lifecycle:
        supervise_missions(pid, mission_run_id, mission_debugger)
        return
    if wait_for_mission_start(pid, mission_log_start, mission_debugger):
        if mission_debugger.poll() is None:
            if native:
                mission_debugger.communicate(input=b"stop\n", timeout=15)
                if mission_debugger.returncode:
                    raise RuntimeError("Native mission debugger could not detach cleanly")
            else:
                mission_debugger.terminate()
                mission_debugger.wait()
            print(f"{time.strftime('%H:%M:%S')} gdb_active_mission.gdb : détaché (mission démarrée)", flush=True)
        chosen = combat_choice()
        print(f"{time.strftime('%H:%M:%S')} correctifs de combat choisis : {', '.join(chosen) or 'aucun'}", flush=True)
        if EXIT_PROBE in chosen:
            with open(os.path.join("data", "logs", "gdb_exit.log"), "a") as log:
                subprocess.Popen(["gdb", "-p", pid, "-batch", "-x", os.path.join(TOOLS, "gdb_exit.gdb")],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            print(f"{time.strftime('%H:%M:%S')} gdb_exit.gdb : posé (data/exit.txt)", flush=True)
        apply_mission_patches(pid, chosen)
        # Keep the Windows parent alive so taskkill /T can stop its follower tree.
        if native:
            while alive(pid):
                time.sleep(1)


if __name__ == "__main__":
    main()
