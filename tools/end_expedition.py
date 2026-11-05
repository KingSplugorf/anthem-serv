"""Usage : python3 -I tools/end_expedition.py PID [poser|retirer|etat] : fait aboutir « End Freeplay / End Expedition » sur le serveur interne. Le maintien déclenche RequestLeave de LeaveExpeditionEntity (globals/transitions/prefabs/triggerendexpedition_prefab) ; ClientLeaveExpeditionEntity (0x14095ee10) poste ExpeditionRequestLeaveMessage, que ServerLeaveExpeditionEntity (0x1481f36e0) transmet à l'exécutant du ServerExpeditionManager ([[0x1446c80e0]+0x48]) ; celui du jeu libre (table 0x143a49910, départ 0x1481f1460) cherche l'expédition du joueur dans sa liste ([exécutant+0x100], fiches ExpeditionDto de 0x48 octets) et, faute d'expédition, sort sans prévenir : ni Success ni Failed. « poser » fait répondre Success quand le joueur n'a pas d'expédition (0x1481f1482 je 0x1481f14da -> je 0x1481f14d2, rappel 0x140a6cea0 avec 1) : Success mène au proxy d'expédition (0x140a65960, événement 0xfba71aa8) qui, sans GUID d'expédition, transfère le joueur au point par défaut du gestionnaire (« VictoryPose_InProc »). « etat » lit le nombre d'expéditions de l'exécutant.

Story executor (0x143a49838): its exact exported leave method at 0x140a611a0
silently returns when its participant list is empty. Redirect only that empty
branch to its own existing Success callback at 0x140a611f0. Nonempty checks are
preserved. Native lifecycle watches both success sites before the game transfers.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from process_memory import open_memory

import struct

NO_EXPEDITION_JUMP = 0x1481F1482
SUCCESS_CALLBACK = 0x1481F14D2
SUCCESS_CALLBACK_CODE = bytes.fromhex("b2014889f9ff5718")
LEAVE_ON_NO_EXPEDITION, SUCCESS_ON_NO_EXPEDITION = bytes.fromhex("7456"), bytes.fromhex("744e")
EXPEDITION_MANAGER, EXECUTOR_OFFSET, EXPEDITIONS_OFFSET, EXPEDITION_SIZE = 0x1446C80E0, 0x48, 0x100, 0x48
STORY_LEAVE_ENTRY = 0x140A611A0
STORY_EMPTY_BRANCH = STORY_LEAVE_ENTRY + 0x1A
STORY_SUCCESS_CALLBACK = STORY_LEAVE_ENTRY + 0x50
# Exact supported-build method exported from the active story executor. Only
# its empty-list jump changes: skip the silent return and use its own callback.
STORY_LEAVE_CODE = bytes.fromhex(
    '488b8120010000448b40fc410fbaf01f4d6bd0584c03d0493bc2742e0f1f4000'
    '48391074094883c058493bc275f2493bc27417488b81200100008b48fc0fbaf11f'
    '83f90175048bc1eb0233c084c07409b201498bc949ff6118c3')
STORY_LEAVE_PATCHED = STORY_LEAVE_CODE[:0x1B] + b'\x34' + STORY_LEAVE_CODE[0x1C:]
STORY_SUCCESS_CODE = STORY_LEAVE_CODE[0x50:0x59]


def patches():
    return [(SUCCESS_CALLBACK, SUCCESS_CALLBACK_CODE, SUCCESS_CALLBACK_CODE),
            (NO_EXPEDITION_JUMP, LEAVE_ON_NO_EXPEDITION, SUCCESS_ON_NO_EXPEDITION),
            (STORY_LEAVE_ENTRY, STORY_LEAVE_CODE, STORY_LEAVE_PATCHED)]


def removal():
    return [(address, patched, original) for address, original, patched in patches()]


def apply_supported(memory, chosen):
    # Recognize older installations with just the freeplay branch patched, but
    # preflight every method before writing anything. Write only differing bytes.
    found = []
    for address, original, patched in chosen:
        if len(original) != len(patched):
            raise ValueError('Leave patches must preserve instruction lengths')
        memory.seek(address)
        current = memory.read(len(original))
        if current not in (original, patched):
            sys.exit(f'octets inattendus en {address:#x} : rien modifié')
        found.append(current)
    changed = False
    for current, (address, _, patched) in zip(found, chosen):
        differences = [index for index, (a, b) in enumerate(zip(current, patched)) if a != b]
        if differences:
            first, last = differences[0], differences[-1] + 1
            memory.seek(address + first)
            memory.write(patched[first:last])
            changed = True
    return 'appliqué' if changed else 'déjà appliqué'


def qword(memory, address):
    memory.seek(address)
    return struct.unpack("<Q", memory.read(8))[0]


def state(memory):
    memory.seek(NO_EXPEDITION_JUMP)
    jump = memory.read(2)
    patched = {LEAVE_ON_NO_EXPEDITION: "non posé", SUCCESS_ON_NO_EXPEDITION: "posé"}.get(jump, f"octets inattendus {jump.hex()}")
    memory.seek(STORY_EMPTY_BRANCH)
    story = memory.read(2)
    story_status = {b'\x74\x2e': 'non posé', b'\x74\x34': 'posé'}.get(story, f'inattendu {story.hex()}')
    patched += f' ; départ histoire sans participant : {story_status}'
    manager = qword(memory, EXPEDITION_MANAGER)
    executor = qword(memory, manager + EXECUTOR_OFFSET) if manager else 0
    if not executor:
        return f"départ sans expédition : {patched} ; pas d'exécutant d'expédition serveur"
    first, last = qword(memory, executor + EXPEDITIONS_OFFSET), qword(memory, executor + EXPEDITIONS_OFFSET + 8)
    return f"départ sans expédition : {patched} ; exécutant {executor:#x} (table {qword(memory, executor):#x}), {(last - first) // EXPEDITION_SIZE} expédition(s)"


def main():
    pid = int(sys.argv[1])
    action = sys.argv[2] if len(sys.argv) > 2 else "poser"
    with open_memory(pid, "r+b" if action != "etat" else "rb") as memory:
        if action == "etat":
            print(state(memory))
            return
        if action not in ("poser", "retirer"):
            sys.exit(__doc__)
        outcome = apply_supported(memory, patches() if action == "poser" else removal())
    print(f"départ d'expédition sans expédition serveur -> {'Success' if action == 'poser' else 'sans réponse (origine)'} : {outcome}")


if __name__ == "__main__":
    main()
