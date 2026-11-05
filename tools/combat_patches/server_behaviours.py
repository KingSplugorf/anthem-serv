"""Usage : python3 -I tools/combat_patches/server_behaviours.py PID {verifier|poser|retirer|etat} : fait appeler par la mise à jour serveur des armes (0x14879d8c0) les comportements cadence, tir, visée et munitions (+0x2b8..+0x2d0, vf20 puis vf30, comme le client en 0x1480e8810), via un code ajouté dans le remplissage int3 en 0x142b27d00, atteint en réécrivant le relais 0x140b371b0 (jmp 0x14879d8c0), car les entités gardent le pointeur lu dans la table à leur création.

The entity must be a ServerActionGearEntity. Each behavior is independently
owner-validated; an absent cadence behavior must not suppress the other slots.
Install only while the previous wrapper is inactive.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "health"))
from health_mirror import Process

CAVE = 0x142B27D00
CAVE_END = 0x142B28000
SLOT = 0x143A5C450
ORIGINAL_TARGET = 0x140B371B0
STUB = 0x140B371B0
STUB_ORIGINAL = b"\xe9" + struct.pack("<i", 0x14879D8C0 - (STUB + 5))
STUB_PATCHED = b"\xe9" + struct.pack("<i", CAVE - (STUB + 5))
SERVER_ACTION_GEAR_TABLE = 0x143A5BF40
BEHAVIOR_SLOTS = range(0x2B8, 0x2D8, 8)
CODE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cave.bin"), "rb").read()


def owned(process: Process, entity: int, behavior: int) -> bool:
    if not behavior or behavior % 8 or not 0x10000 <= behavior < 0x7FF000000000:
        return False
    try:
        owner, table = process.qword(behavior + 0x10), process.qword(behavior)
    except OSError:
        return False
    return 0 <= owner - entity < 0x460 and 0x143660000 <= table < 0x1442F0000


def preflight(process: Process) -> bool:
    safe = True
    found, _ = process.instances([SERVER_ACTION_GEAR_TABLE], process.regions())
    for entity in found[SERVER_ACTION_GEAR_TABLE]:
        slots = {offset: process.qword(entity + offset) for offset in BEHAVIOR_SLOTS}
        risky = [f"{offset:#x}={value:#x}" for offset, value in slots.items() if value and not owned(process, entity, value) and 0x10000 <= value < 0x7FF000000000 and value % 8 == 0]
        active = any(owned(process,entity,value) for value in slots.values())
        print(f"{entity:#x} {'actif' if active else 'ignoré'}{' risque ' + ' '.join(risky) if risky else ''}")
        safe &= not risky
    return safe


def main() -> None:
    pid, action = int(sys.argv[1]), sys.argv[2]
    process = Process(pid)
    slot = process.qword(SLOT)
    cave = process.read(CAVE, len(CODE))
    if action == "etat":
        stub = process.read(STUB, 5)
        print(f"entrée {SLOT:#x} = {slot:#x} ; relais {stub.hex()} ({'dévié' if stub == STUB_PATCHED else 'origine' if stub == STUB_ORIGINAL else 'inattendu'}) ; code {'posé' if cave == CODE else 'absent' if set(cave) == {0xCC} else 'inattendu'}")
    elif action == "verifier":
        print("sans risque" if preflight(process) else "valeurs douteuses : ne pas poser")
    elif action == "poser":
        if slot not in (ORIGINAL_TARGET, CAVE):
            sys.exit(f"entrée inattendue {slot:#x}")
        if CAVE+len(CODE) > CAVE_END:
            sys.exit("cave.bin dépasse son emplacement")
        stub = process.read(STUB,5)
        if stub not in (STUB_ORIGINAL,STUB_PATCHED):
            sys.exit(f"relais inattendu {stub.hex()}")
        if cave != CODE and set(cave) != {0xCC}:
            sys.exit("autre version du code en mémoire : relancer le jeu")
        if not preflight(process):
            sys.exit("valeurs douteuses : rien posé")
        if cave != CODE:
            process.write(CAVE, CODE)
        if slot != ORIGINAL_TARGET:
            process.write(SLOT, struct.pack("<Q", ORIGINAL_TARGET))
        process.write(STUB, STUB_PATCHED)
        print(f"posé : relais {STUB:#x} {stub.hex()} -> {process.read(STUB, 5).hex()}")
    elif action == "retirer":
        process.write(STUB, STUB_ORIGINAL)
        process.write(SLOT, struct.pack("<Q", ORIGINAL_TARGET))
        print(f"retiré : relais {STUB:#x} -> {process.read(STUB, 5).hex()}, entrée {SLOT:#x} -> {ORIGINAL_TARGET:#x} (code laissé en place, inactif)")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
