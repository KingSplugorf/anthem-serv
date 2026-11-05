"""Usage : python3 -I tools/end_expedition.py PID [poser|retirer|etat] : fait aboutir « End Freeplay / End Expedition » sur le serveur interne. Le maintien déclenche RequestLeave de LeaveExpeditionEntity (globals/transitions/prefabs/triggerendexpedition_prefab) ; ClientLeaveExpeditionEntity (0x14095ee10) poste ExpeditionRequestLeaveMessage, que ServerLeaveExpeditionEntity (0x1481f36e0) transmet à l'exécutant du ServerExpeditionManager ([[0x1446c80e0]+0x48]) ; celui du jeu libre (table 0x143a49910, départ 0x1481f1460) cherche l'expédition du joueur dans sa liste ([exécutant+0x100], fiches ExpeditionDto de 0x48 octets) et, faute d'expédition, sort sans prévenir : ni Success ni Failed. « poser » fait répondre Success quand le joueur n'a pas d'expédition (0x1481f1482 je 0x1481f14da -> je 0x1481f14d2, rappel 0x140a6cea0 avec 1) : Success mène au proxy d'expédition (0x140a65960, événement 0xfba71aa8) qui, sans GUID d'expédition, transfère le joueur au point par défaut du gestionnaire (« VictoryPose_InProc »). « etat » lit le nombre d'expéditions de l'exécutant."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from process_memory import open_memory

import os
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from launch_from_hub import apply  # noqa: E402

NO_EXPEDITION_JUMP = 0x1481F1482
SUCCESS_CALLBACK = 0x1481F14D2
SUCCESS_CALLBACK_CODE = bytes.fromhex("b2014889f9ff5718")
LEAVE_ON_NO_EXPEDITION, SUCCESS_ON_NO_EXPEDITION = bytes.fromhex("7456"), bytes.fromhex("744e")
EXPEDITION_MANAGER, EXECUTOR_OFFSET, EXPEDITIONS_OFFSET, EXPEDITION_SIZE = 0x1446C80E0, 0x48, 0x100, 0x48


def patches():
    return [(SUCCESS_CALLBACK, SUCCESS_CALLBACK_CODE, SUCCESS_CALLBACK_CODE), (NO_EXPEDITION_JUMP, LEAVE_ON_NO_EXPEDITION, SUCCESS_ON_NO_EXPEDITION)]


def removal():
    return [(SUCCESS_CALLBACK, SUCCESS_CALLBACK_CODE, SUCCESS_CALLBACK_CODE), (NO_EXPEDITION_JUMP, SUCCESS_ON_NO_EXPEDITION, LEAVE_ON_NO_EXPEDITION)]


def qword(memory, address):
    memory.seek(address)
    return struct.unpack("<Q", memory.read(8))[0]


def state(memory):
    memory.seek(NO_EXPEDITION_JUMP)
    jump = memory.read(2)
    patched = {LEAVE_ON_NO_EXPEDITION: "non posé", SUCCESS_ON_NO_EXPEDITION: "posé"}.get(jump, f"octets inattendus {jump.hex()}")
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
        outcome = apply(memory, patches() if action == "poser" else removal())
    print(f"départ d'expédition sans expédition serveur -> {'Success' if action == 'poser' else 'sans réponse (origine)'} : {outcome}")


if __name__ == "__main__":
    main()
