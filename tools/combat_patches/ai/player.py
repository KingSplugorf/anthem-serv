"""Usage : python3 -I player.py PID {trouver|poser|etat} : retrouve le personnage serveur du joueur local et ses copies clientes. Le personnage est celui du seul joueur dont la connexion est locale (player_list.local_characters : client dans ce processus) ; si le serveur n'est pas lisible, le premier ServerDylanCharacter dont [entité+0x2b8] (son ServerPlayer) est non nul et dont le nom [[entité+0x320]+0x20] contient EXOPlayer, trouvé par une recherche en mémoire. Copies clientes (même recherche) : ClientDylanHealthComponent, table 0x143a8cec0, s'il est unique et que le serveur n'a qu'un joueur (avec deux joueurs, la copie unique pourrait être celle du joueur distant), et l'état [+0x58] de son ClientRegeneratingShieldComponent, table 0x143a71690. « trouver » les affiche sans rien écrire ; « poser » les écrit dans les cases lues par le code ajouté : 0x142b2abf0 (personnage : target.s, shots.s, behaviours.s, perception.s), 0x142b2abe8 (vie cliente) et 0x142b2abe0 (bouclier client : shots.s), seulement si le personnage est trouvé ; « etat » lit ces cases. Module partagé par target.py, shots.py, player_position.py et auto_patches.py.

Client copies are selected only after character/component owner links and list
membership are validated. Multiple owner-validated copies remain ambiguous.
"""

import os
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "health"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from health_mirror import Process
import player_list

PLAYER_SLOT = 0x142B2ABF0
CLIENT_HEALTH_SLOT = 0x142B2ABE8
CLIENT_SHIELD_SLOT = 0x142B2ABE0
SERVER_CHARACTER = 0x143A75900
CLIENT_HEALTH = 0x143A8CEC0
CLIENT_CHARACTER = 0x143A6FF30
CLIENT_SHIELD = 0x143A71690
TABLES = [SERVER_CHARACTER, CLIENT_HEALTH]


def player(process: Process, characters: list[int], local: list[int] | None) -> int:
    if local is not None:
        return local[0] if len(local) == 1 else 0
    for entity in characters:
        try:
            if process.qword(entity + 0x2B8) and b"EXOPlayer" in process.read(process.qword(process.qword(entity + 0x320) + 0x20), 80):
                return entity
        except OSError:
            continue
    return 0


def client_character(process: Process, health: int) -> int:
    """Require an owned live component, not just a vtable value in writable memory."""
    try:
        if not health or process.qword(health) != CLIENT_HEALTH:
            return 0
        block = process.qword(health + 8)
        entity = process.qword(block)
        if process.qword(entity) != CLIENT_CHARACTER or process.qword(entity + 0x58) != block:
            return 0
        count = process.read(block + 8, 1)[0]
        if any(process.qword(block + 0x20 + index * 0x20) == health for index in range(count)):
            return entity
    except OSError:
        pass
    return 0


def client_copies(process: Process, healths: list[int]) -> tuple[int, int]:
    live = [health for health in sorted(set(healths)) if client_character(process, health)]
    if len(live) != 1:
        return 0, 0
    health = live[0]
    try:
        components = process.qword(health + 8)
        for index in range(process.read(components + 8, 1)[0]):
            component = process.qword(components + 0x20 + index * 0x20)
            if component and process.qword(component) == CLIENT_SHIELD and process.qword(component + 8) == components:
                return health, process.qword(component + 0x58)
    except OSError:
        pass
    return health, 0


def find(process: Process, found: dict[int, list[int]] | None = None) -> tuple[int, int, int]:
    if found is None:
        found, _ = process.instances(TABLES, process.regions())
    everyone = player_list.server_players(process)
    alone = everyone is None or sum(1 for _, _, _, character in everyone if character) <= 1
    candidates = found[CLIENT_HEALTH]
    owned = [health for health in sorted(set(candidates)) if client_character(process, health)]
    copies = client_copies(process, owned) if alone else (0, 0)
    print(f"{time.strftime('%H:%M:%S')} client player health: {len(candidates)} candidates, {len(owned)} owner-validated, single-player {alone}, selected {copies[0]:#x}", flush=True)
    return player(process, found[SERVER_CHARACTER], player_list.local_characters(process)), *copies


def write_slots(process: Process, character: int, health: int, shield: int) -> None:
    process.write(PLAYER_SLOT, struct.pack("<Q", character))
    process.write(CLIENT_HEALTH_SLOT, struct.pack("<Q", health))
    process.write(CLIENT_SHIELD_SLOT, struct.pack("<Q", shield))


def main() -> None:
    pid, action = int(sys.argv[1]), sys.argv[2]
    process = Process(pid)
    if action == "etat":
        print(f"joueur {process.qword(PLAYER_SLOT):#x} ; copies clientes vie {process.qword(CLIENT_HEALTH_SLOT):#x}, bouclier {process.qword(CLIENT_SHIELD_SLOT):#x}")
        return
    if action not in ("trouver", "poser"):
        sys.exit(__doc__)
    character, health, shield = find(process)
    print(f"joueur {character:#x} ; copies clientes vie {health:#x}, bouclier {shield:#x}")
    if action == "poser":
        if not character:
            sys.exit("personnage serveur du joueur introuvable : rien écrit")
        write_slots(process, character, health, shield)


if __name__ == "__main__":
    main()
