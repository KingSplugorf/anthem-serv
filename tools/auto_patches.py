"""Usage : python3 -I tools/auto_patches.py PID : applique seul, au bon moment, les correctifs qui font jouer les missions sur le serveur interne du jeu : redirection du lancement d'une mission et créateur serveur de l'entité de navigation (ai/navigation.py, dès que le code est en mémoire, avant le premier hub : sans entité NavPower serveur, les conversations scénarisées du hub s'arrêtent aussitôt), départ d'expédition sans expédition serveur (end_expedition.py : « End Freeplay / End Expedition » répond Success et transfère au point VictoryPose_InProc), points de mission en serveur interne (dès que le niveau racine est chargé, au menu de démarrage), relevé du point de lancement de chaque quête (default_point.py --quetes, data/quest_points_PID.json), puis, dès que GameStartingInfo existe, pose de tools/gdb_active_mission.gdb qui, au lancement d'une mission depuis le hub ou du prologue depuis le menu de démarrage, démarre le serveur interne sur le niveau racine au point de lancement de la quête et passe GameStartingInfo en mode Mission avec la quête demandée (le message GameStarted du serveur dédié manque) ; au premier démarrage de mission de ce jeu (état 8 dans data/active_mission_PID.txt, relu toutes les 0,2 s), détache gdb_mission_active (un seul lancement de mission par démarrage du jeu) et pose aussitôt, en trois files parallèles, créations, mort, comportements d’arme serveur et SetHealth serveur publié et vie en points (published_health.py), puis ApplyDamage natif (damage.py) et réactions aux dégâts répliqués côté client (reactions.py : animation de mort des ennemis) (après leur vérification en mémoire), les rappels serveur (impacts, vie, sections, refusés tant que le serveur ne les a pas enregistrés) et les correctifs d'IA (target.py et shots.py sans player, déplacements, perception), pendant qu'une seule lecture de la mémoire cherche le personnage serveur du player et ses copies clientes (player.py) ; écrit ensuite le player et lance le suivi de la position du player (ai/player_position.py, qui le cherche à nouveau s'il manque) et celui des boucliers ennemis et des objets destructibles (health/health_follow.py, data/logs/health_follow.log) ; ce qui échoue (serveur ou player pas encore prêts) est retenté toutes les 2 s pendant 10 min ; data/combat_patches.txt, s'il existe, restreint cette liste (un nom de script de tools/combat_patches/ par ligne, « suivi » pour le suivi de la vie, « sonde_sortie » pour remplacer gdb_mission_active par tools/gdb_exit.gdb) ; MISSION_PAIR=1 pose la redirection pair (launch_from_hub.py --pair, docs/etude-multijoueur/transport.md), identique sur l'hôte et sur le jeu qui rejoint : seul l'hôte de topologie de la partie Blaze démarre son serveur interne, donc gdb_mission_active et les correctifs de mission n'agissent que sur lui ; le hub partagé (HUB_PARTAGE, docs/etude-multijoueur/social.md) n'ajoute rien ici : le hub démarre en solo, et c'est l'ouverture par Blaze (tools/hub_partage.sh) qui le recharge en hôte pair par la redirection pair"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from process_memory import open_memory, alive as process_alive

import os
import json
import re
import subprocess
import time
import threading
import uuid
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


def apply_in_order(pid, patches):
    for index, (script, arguments) in enumerate(patches):
        result = subprocess.run([sys.executable, "-X", "utf8", "-I", os.path.join(COMBAT, script), pid, "poser", *arguments],
                                capture_output=True, text=True, encoding="utf-8")
        last_line = ((result.stdout + result.stderr).strip().splitlines() or [""])[-1]
        print(f"{time.strftime('%H:%M:%S')} {script} : {last_line}", flush=True)
        if result.returncode:
            return patches[index:]
    return ()


def follow(pid, chosen):
    if "ai/player_position.py" in chosen or HEALTH_FOLLOW in chosen:
        with open(os.path.join("data", "logs", "player_position.log"), "a") as log:
            subprocess.Popen([sys.executable, "-X", "utf8", "-I", os.path.join(COMBAT, "ai", "player_position.py"), pid, "--duree", str(HEALTH_FOLLOW_SECONDS)],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        print(f"{time.strftime('%H:%M:%S')} player_position.py : lancé", flush=True)
    if HEALTH_FOLLOW not in chosen:
        return
    with open(os.path.join("data", "logs", "health_follow.log"), "a") as log:
        subprocess.Popen([sys.executable, "-X", "utf8", "-I", os.path.join(COMBAT, "health", "health_follow.py"), pid, "--duree", str(HEALTH_FOLLOW_SECONDS)],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    print(f"{time.strftime('%H:%M:%S')} health_follow.py : lancé", flush=True)


def find_player(process):
    import player
    character, health, shield = player.find(process)
    if character:
        player.write_slots(process, character, health, shield)
    print(f"{time.strftime('%H:%M:%S')} player.py : player {character:#x}, copies clientes vie {health:#x}, bouclier {shield:#x}"
          f"{'' if character else ' (laissé à player_position.py)'}", flush=True)


def apply_mission_patches(pid, chosen):
    process = Process(int(pid))
    groups = [tuple(patch for patch in group if patch[0] in chosen) for group in MISSION_GROUPS]
    player_pending = True
    deadline = time.monotonic() + MISSION_RETRY_LIMIT
    with ThreadPoolExecutor(len(groups) + 1) as pool:
        while alive(pid):
            search = pool.submit(find_player, process) if player_pending else None
            groups = list(pool.map(lambda group: apply_in_order(pid, group), groups))
            if search:
                search.result()
                player_pending = False
                follow(pid, chosen)
            if not any(groups):
                print(f"{time.strftime('%H:%M:%S')} correctifs de mission posés", flush=True)
                return
            if time.monotonic() >= deadline:
                print(f"{time.strftime('%H:%M:%S')} abandon après {MISSION_RETRY_LIMIT} s : {', '.join(script for group in groups for script, _ in group)}", flush=True)
                raise RuntimeError("Mission patches did not finish before the retry deadline")
            time.sleep(MISSION_RETRY_SECONDS)


def launch_steps(environment):
    redirection = ("launch_from_hub.py", "--pair") if environment.get(PEER_HOST) == "1" else ("launch_from_hub.py",)
    return [redirection, (NAVIGATION, "poser"), ("end_expedition.py", "poser"), ("missions_inproc.py",), ("default_point.py", "--quetes")]


def step_done(pid, step):
    result = run(step[0], pid, *step[1:])
    return mission_points_found(result.stdout) if step[0] == "missions_inproc.py" else result.returncode == 0


def main():
    pid = sys.argv[1]
    os.makedirs(os.path.join("data", "logs"), exist_ok=True)
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
    if not until(pid, CODE_RETRY_SECONDS, lambda: starting_info_ready(pid)):
        return
    mission_log_start = os.path.getsize(mission_log(pid)) if os.path.exists(mission_log(pid)) else 0
    native = os.name == "nt"
    tls_diagnostics = native and os.environ.get("ANTHEM_TLS_DIAGNOSTICS") == "1"
    debugger = ([sys.executable, "-X", "utf8", "-I", os.path.join(TOOLS, "windows_debugger.py"), pid]
                if native else ["gdb", "-p", pid, "-batch", "-x", os.path.join(TOOLS, "gdb_active_mission.gdb")])
    run_id = uuid.uuid4().hex if tls_diagnostics else None
    debugger_env = {**os.environ, "ANTHEM_TLS_RUN_ID": run_id} if tls_diagnostics else None
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
    print(f"{time.strftime('%H:%M:%S')} {'Win32' if native else 'GDB'} mission debugger : posé", flush=True)
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
