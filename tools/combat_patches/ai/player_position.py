"""Usage : python3 -I player_position.py PID [--duree SECONDES] : recopie 20 fois/s la position réelle du player dans son personnage serveur. Elle n'écrit que dans le personnage du player local : celui dont le ServerPlayer ([personnage+0x2b8]) est sur une connexion du ServerPeer qui passe par la socket mémoire (client dans ce processus, player_list.local_characters), vérifié chaque seconde ; jamais dans celui d'un player distant, ni quand le serveur est illisible (le personnage gardé est alors tenu pour invalide et recherché de nouveau). Le ServerDylanCharacter du player (écrit par target.py en 0x142b2abf0) garde en +0x1b0 sa position d'apparition : le serveur interne ne reçoit pas le déplacement du player, donc les IA (but de MoveToPoint, RangeToTarget de target.s) visent un point figé. La position écrite est la translation de la transformation de la copie cliente du player (ClientDylanCharacter retrouvé par sa vie cliente en 0x142b2abe8, bloc [entité+0x58]), égale à son ClientDylanCharacterSimulationComponent ; à défaut, l'origine de visée serveur [[personnage+0x7a8]+0x150] abaissée de 6,4 m (relevé du 2026-10-31 21:5x : elle dérive de 3 m/s, player immobile, à 100 m du player, soupçonnée de faire annuler la réparation des Freelancers) ; elle est écrite, si ce composant appartient bien encore au personnage (même bloc de composants [composant+8] = [personnage+0x58] et présent dans sa liste, pour ne jamais écrire dans un personnage libéré), en +0x1b0 et dans la translation de la transformation de l'entité (bloc [personnage+0x58], ligne (octet +9 + 2 × octet +0xa) × 32 + 0x10, translation +0x30, lue par getTransform 0x146c94a20 et donc par TransformProvider_Entity des IA). Si le personnage gardé en 0x142b2abf0 n'est pas valide (nul au départ, sans attendre ; sinon pendant 3 s : table, composant de visée qui ne lui appartient plus : mort, réapparition, changement de zone), player.py le retrouve avec ses copies clientes en une seule recherche en mémoire et réécrit les trois cases ; après une recherche vaine, la suivante attend 3 s, puis le double à chaque échec, jusqu'à 60 s (hub après la mission).

On native Windows, an unavailable client copy leaves native server coordinates
untouched; the unverified aim-origin fallback is retained only for legacy Linux.
A missing copy is retried at most once per 60 seconds through a separate read-only
handle. Results are committed only while the same local character remains current.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from process_memory import alive as process_alive

import os
import struct
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "health"))
sys.path.insert(0, HERE)
from health_mirror import Process
import player
import player_list

AIMING_COMPONENT = 0x143A88FF0
CLIENT_CHARACTER = 0x143A6FF30
EYE_HEIGHT = 6.4
LOST_SECONDS = 3.0
LONGEST_RETRY_SECONDS = 60.0
LOCAL_CHECK_SECONDS = 1.0


def transform_translation(process: Process, entity: int) -> int:
    block = process.qword(entity + 0x58)
    index = process.read(block + 9, 1)[0] + process.read(block + 0xA, 1)[0] * 2
    return block + index * 32 + 0x10 + 0x30


def owned_by(process: Process, component: int, entity: int) -> bool:
    block = process.qword(entity + 0x58)
    if process.qword(component + 8) != block:
        return False
    count = process.read(block + 8, 1)[0]
    return any(process.qword(block + 0x20 + index * 0x20) == component for index in range(count))


def local(process: Process, character: int) -> bool:
    found = player_list.local_characters(process)
    return found is not None and character in found


def client_position(process: Process) -> tuple[float, float, float] | None:
    try:
        health = process.qword(player.CLIENT_HEALTH_SLOT)
        entity = player.client_character(process, health)
        if not entity:
            return None
        return struct.unpack("<3f", process.read(transform_translation(process, entity), 12))
    except OSError:
        return None


def aimed_position(process: Process, aiming: int) -> tuple[float, float, float]:
    x, y, z = struct.unpack("<3f", process.read(aiming + 0x150, 12))
    return x, y - EYE_HEIGHT, z


def refresh_player(process: Process) -> bool:
    character, health, shield = player.find(process)
    print(f"{time.strftime('%H:%M:%S')} recherche : player {character:#x}, copies clientes vie {health:#x}, bouclier {shield:#x}", flush=True)
    if character:
        player.write_slots(process, character, health, shield)
    return bool(character)


def discover_client_copies(pid):
    # A separate handle keeps this scan's seek/read cursor away from the
    # follower's writes. The discovery process has read-only access.
    probe = Process(pid, writable=False)
    try:
        return player.find(probe)
    finally:
        probe.memory.close()


def accept_client_copies(process, character, result):
    found, health, _ = result
    try:
        if (found != character or process.qword(player.PLAYER_SLOT) != character
                or not local(process, character)):
            return False
        validated, shield = player.client_copies(process, [health])
        if validated != health or not validated:
            return False
        process.write(player.CLIENT_HEALTH_SLOT, struct.pack('<Q', validated))
        process.write(player.CLIENT_SHIELD_SLOT, struct.pack('<Q', shield))
        print(f"{time.strftime('%H:%M:%S')} position client retrouvée : vie {validated:#x}, bouclier {shield:#x}", flush=True)
        return True
    except OSError:
        return False


def follow_positions(process, pid, duration, copy_pool):
    deadline = time.monotonic() + duration
    copies = 0
    copy_future, next_copy_scan = None, 0.0
    waiting_for_client = False
    search_at, retry = None, LOST_SECONDS
    checked, checked_at, is_local = 0, 0.0, False
    while time.monotonic() < deadline and process_alive(pid):
        try:
            character = process.qword(player.PLAYER_SLOT)
            try:
                aiming = process.qword(character + 0x7A8) if character and process.qword(character) == player.SERVER_CHARACTER else 0
                valid = bool(aiming) and process.qword(aiming) == AIMING_COMPONENT and owned_by(process, aiming, character)
            except OSError:
                aiming, valid = 0, False
            if valid and (character != checked or time.monotonic() - checked_at >= LOCAL_CHECK_SECONDS):
                was_local = is_local or character != checked
                is_local = local(process, character)
                if was_local and not is_local:
                    print(f"{time.strftime('%H:%M:%S')} personnage {character:#x} non établi comme celui du player local (connexion distante ou serveur illisible) : position non écrite", flush=True)
                checked, checked_at = character, time.monotonic()
            valid = valid and is_local
            if not valid:
                search_at = search_at or time.monotonic() + (0.0 if character in (0, 0xCCCCCCCCCCCCCCCC) else LOST_SECONDS)
                if time.monotonic() >= search_at:
                    print(f"{time.strftime('%H:%M:%S')} personnage du player {character:#x} invalide : recherche du nouveau", flush=True)
                    found = refresh_player(process)
                    search_at = None if found else time.monotonic() + retry
                    retry = LOST_SECONDS if found else min(2 * retry, LONGEST_RETRY_SECONDS)
            else:
                search_at, retry = None, LOST_SECONDS
            if valid:
                client = client_position(process)
                if copy_future is not None and copy_future.done():
                    completed, copy_future = copy_future, None
                    try:
                        result = completed.result()
                        if client is None and accept_client_copies(process, character, result):
                            client = client_position(process)
                    except OSError as error:
                        print(f"{time.strftime('%H:%M:%S')} recherche client à réessayer : {error}", flush=True)
                if client is None and copy_future is None and time.monotonic() >= next_copy_scan:
                    copy_future = copy_pool.submit(discover_client_copies, pid)
                    next_copy_scan = time.monotonic() + LONGEST_RETRY_SECONDS
                # The Linux workaround's aim origin is not a verified player
                # position. On native Windows it can overwrite correct movement
                # and repeatedly cancel proximity/hold interactions.
                position_source = client if client is not None else (
                    aimed_position(process, aiming) if os.name != 'nt' else None)
                if position_source is None and not waiting_for_client:
                    print(f"{time.strftime('%H:%M:%S')} client position unavailable: preserving native server position while retrying lookup", flush=True)
                    waiting_for_client = True
                if position_source is not None:
                    waiting_for_client = False
                x, y, z = position_source or (0.0, 0.0, 0.0)
                if abs(x) < 1e5 and abs(y) < 1e5 and abs(z) < 1e5 and (x, y, z) != (0.0, 0.0, 0.0):
                    position = struct.pack("<3f", x, y, z)
                    process.memory.seek(character + 0x1B0)
                    process.memory.write(position)
                    process.memory.seek(transform_translation(process, character))
                    process.memory.write(position)
                    copies += 1
                    if copies % 1200 == 1:
                        print(f"{time.strftime('%H:%M:%S')} player {x:.1f} {y:.1f} {z:.1f}", flush=True)
        except OSError:
            pass
        time.sleep(0.05)


def main() -> None:
    pid = int(sys.argv[1])
    duration = float(sys.argv[sys.argv.index("--duree") + 1]) if "--duree" in sys.argv else 4 * 3600.0
    process = Process(pid)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        follow_positions(process, pid, duration, pool)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        process.memory.close()


if __name__ == "__main__":
    main()
