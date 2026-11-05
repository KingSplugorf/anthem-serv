"""Usage : python3 -I player_position.py PID [--duree SECONDES] : recopie 20 fois/s la position réelle du player dans son personnage serveur. Elle n'écrit que dans le personnage du player local : celui dont le ServerPlayer ([personnage+0x2b8]) est sur une connexion du ServerPeer qui passe par la socket mémoire (client dans ce processus, player_list.local_characters), vérifié chaque seconde ; jamais dans celui d'un player distant, ni quand le serveur est illisible (le personnage gardé est alors tenu pour invalide et recherché de nouveau). Le ServerDylanCharacter du player (écrit par target.py en 0x142b2abf0) garde en +0x1b0 sa position d'apparition : le serveur interne ne reçoit pas le déplacement du player, donc les IA (but de MoveToPoint, RangeToTarget de target.s) visent un point figé. La position écrite est la translation de la transformation de la copie cliente du player (ClientDylanCharacter retrouvé par sa vie cliente en 0x142b2abe8, bloc [entité+0x58]), égale à son ClientDylanCharacterSimulationComponent ; à défaut, l'origine de visée serveur [[personnage+0x7a8]+0x150] abaissée de 6,4 m (relevé du 2026-10-31 21:5x : elle dérive de 3 m/s, player immobile, à 100 m du player, soupçonnée de faire annuler la réparation des Freelancers) ; elle est écrite, si ce composant appartient bien encore au personnage (même bloc de composants [composant+8] = [personnage+0x58] et présent dans sa liste, pour ne jamais écrire dans un personnage libéré), en +0x1b0 et dans la translation de la transformation de l'entité (bloc [personnage+0x58], ligne (octet +9 + 2 × octet +0xa) × 32 + 0x10, translation +0x30, lue par getTransform 0x146c94a20 et donc par TransformProvider_Entity des IA). Si le personnage gardé en 0x142b2abf0 n'est pas valide (nul au départ, sans attendre ; sinon pendant 3 s : table, composant de visée qui ne lui appartient plus : mort, réapparition, changement de zone), player.py le retrouve avec ses copies clientes en une seule recherche en mémoire et réécrit les trois cases ; après une recherche vaine, la suivante attend 3 s, puis le double à chaque échec, jusqu'à 60 s (hub après la mission)."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from process_memory import alive as process_alive

import os
import struct
import time

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
        entity = process.qword(process.qword(health + 8)) if health else 0
        if not entity or process.qword(entity) != CLIENT_CHARACTER:
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


def main() -> None:
    pid = int(sys.argv[1])
    duration = float(sys.argv[sys.argv.index("--duree") + 1]) if "--duree" in sys.argv else 4 * 3600.0
    process = Process(pid)
    deadline = time.monotonic() + duration
    copies = 0
    search_at, retry = None, LOST_SECONDS
    checked, checked_at, is_local = 0, 0.0, False
    while time.monotonic() < deadline and process_alive(pid):
        try:
            player = process.qword(player.PLAYER_SLOT)
            try:
                aiming = process.qword(player + 0x7A8) if player and process.qword(player) == player.SERVER_CHARACTER else 0
                valid = bool(aiming) and process.qword(aiming) == AIMING_COMPONENT and owned_by(process, aiming, player)
            except OSError:
                aiming, valid = 0, False
            if valid and (player != checked or time.monotonic() - checked_at >= LOCAL_CHECK_SECONDS):
                was_local = is_local or player != checked
                is_local = local(process, player)
                if was_local and not is_local:
                    print(f"{time.strftime('%H:%M:%S')} personnage {player:#x} non établi comme celui du player local (connexion distante ou serveur illisible) : position non écrite", flush=True)
                checked, checked_at = player, time.monotonic()
            valid = valid and is_local
            if not valid:
                search_at = search_at or time.monotonic() + (0.0 if player in (0, 0xCCCCCCCCCCCCCCCC) else LOST_SECONDS)
                if time.monotonic() >= search_at:
                    print(f"{time.strftime('%H:%M:%S')} personnage du player {player:#x} invalide : recherche du nouveau", flush=True)
                    found = refresh_player(process)
                    search_at = None if found else time.monotonic() + retry
                    retry = LOST_SECONDS if found else min(2 * retry, LONGEST_RETRY_SECONDS)
            else:
                search_at, retry = None, LOST_SECONDS
            if valid:
                x, y, z = client_position(process) or aimed_position(process, aiming)
                if abs(x) < 1e5 and abs(y) < 1e5 and abs(z) < 1e5 and (x, y, z) != (0.0, 0.0, 0.0):
                    position = struct.pack("<3f", x, y, z)
                    process.memory.seek(player + 0x1B0)
                    process.memory.write(position)
                    process.memory.seek(transform_translation(process, player))
                    process.memory.write(position)
                    copies += 1
                    if copies % 1200 == 1:
                        print(f"{time.strftime('%H:%M:%S')} player {x:.1f} {y:.1f} {z:.1f}", flush=True)
        except OSError:
            pass
        time.sleep(0.05)


if __name__ == "__main__":
    main()
