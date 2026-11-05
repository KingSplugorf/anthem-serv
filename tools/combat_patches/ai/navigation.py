"""Usage : python3 -I navigation.py PID {poser [DONNEES]|retirer|etat} : fait créer côté serveur, par le jeu et pour chaque sous-niveau, l'entité de navigation que l'exécutable client ne crée plus. Le créateur serveur de PathfindingSystemEntityData (table 0x143b78748, emplacement 0x143b78760 → 0x1417c72c0 → 0x14a9bf200 obfusqué) ne construit jamais de ServerNavPowerSystemEntity, donc le maillage du niveau, chargé dans le système NavPower serveur [0x1446e5a78], n'est jamais enregistré (liste +0x55d8 vide) : la vf 0x180 des IA (0x149083680) reste fausse et le contrôleur des personnages d'IA n'a pas de maillage. « poser » dévie cet emplacement vers navigation_creator.s (0x142b2bd00), copie du créateur client 0x14a9c42b0 avec le constructeur serveur 0x1417c6db0 : l'entité est allouée dans l'arène du sous-niveau, rattachée à sa collection, donc détruite avec lui par le jeu (destructeur 0x1417c6f10, qui désenregistre les maillages avant la libération du blob) et recréée au niveau suivant ; appels comptés en 0x153737490, dernière entité créée en 0x153737498 (peut-être détruite depuis). Repli transitoire, si DONNEES ([ClientNavPowerSystemEntity+0x28], vérifiées) est donné et que le créateur n'a encore jamais été appelé : navigation.s (0x142b2bc00, entrée 0x143acd550 déviée) construit au premier appel de la vf 0x180 sur le fil serveur une entité statique en 0x153737440 (état en 0x153737400 : 0 en attente, 1 en cours, 2 construite), sans effet si le créateur a été appelé ; cette entité-là n'est jamais détruite et plante le jeu au changement de niveau (2026-11-01 09:21, maillage libéré parcouru en 0x14bd285b2). « retirer » remet les deux emplacements d'origine sans détruire d'entité."""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "health"))
from health_mirror import Process

CODE_ADDRESS = 0x142B2BC00
CREATOR_CODE_ADDRESS = 0x142B2BD00
STATE = 0x153737400
DATA_SLOT = 0x153737408
ENTITY = 0x153737440
CREATOR_CALLS = 0x153737490
CREATOR_LAST = 0x153737498
DATA_END = 0x1537374A0
SLOT = 0x143ACD550
ORIGINAL = 0x140EEE8F0
CREATOR_SLOT = 0x143B78760
CREATOR_ORIGINAL = 0x1417C72C0
CLIENT_NAVIGATION_ENTITY = 0x143B78B18
SERVER_NAVIGATION_ENTITY = 0x143B78A50
PATHFINDING_DATA_TABLE = 0x143B79500
PATHFINDING_DATA_TYPE = 0x14517BF70
SERVER_NAVPOWER = 0x1446E5A78
with open(os.path.join(HERE, "navigation.bin"), "rb") as source:
    CODE = source.read()
with open(os.path.join(HERE, "navigation_creator.bin"), "rb") as source:
    CREATOR_CODE = source.read()


def navpower_state(process: Process) -> str:
    system = process.qword(SERVER_NAVPOWER)
    if not system:
        return "système NavPower serveur pas encore initialisé"
    first, last = process.qword(system + 0x55D8), process.qword(system + 0x55E0)
    meshes = [process.qword(address) for address in range(first, last, 8)]
    types = [struct.unpack("<I", process.read(mesh + 0x96C8, 4))[0] for mesh in meshes]
    return f"entités serveur {struct.unpack('<I', process.read(system + 0x28, 4))[0]}, maillages {len(meshes)} (types {types})"


def creator_state(process: Process) -> str:
    calls = struct.unpack("<I", process.read(CREATOR_CALLS, 4))[0]
    last = process.qword(CREATOR_LAST)
    try:
        alive = last and process.qword(last) == SERVER_NAVIGATION_ENTITY
    except OSError:
        alive = False
    return f"créateur appelé {calls} fois, dernière entité {last:#x}{' (vivante)' if alive else ''}"


def is_level_data(process: Process, data: int) -> bool:
    try:
        return process.qword(data) == PATHFINDING_DATA_TABLE and process.qword(data + 0x10) == PATHFINDING_DATA_TYPE
    except OSError:
        return False


def level_data(process: Process, entities: list[int]) -> int:
    for entity in entities:
        try:
            data = process.qword(entity + 0x28)
        except OSError:
            continue
        if is_level_data(process, data):
            return data
    return 0


def place_code(process: Process, address: int, code: bytes) -> None:
    present = process.read(address, len(code))
    if present != code:
        if set(present) != {0xCC}:
            sys.exit(f"emplacement du code {address:#x} non vide")
        process.write(address, code)


def place_creator(process: Process) -> None:
    current = process.qword(CREATOR_SLOT)
    if current == CREATOR_CODE_ADDRESS:
        return
    if current != CREATOR_ORIGINAL:
        sys.exit(f"créateur inattendu : {current:#x}")
    if any(process.read(STATE, DATA_END - STATE)):
        sys.exit("zone de données non vide")
    place_code(process, CREATOR_CODE_ADDRESS, CREATOR_CODE)
    process.write(CREATOR_SLOT, struct.pack("<Q", CREATOR_CODE_ADDRESS))


def place_fallback(process: Process, data: int) -> str:
    if struct.unpack("<I", process.read(CREATOR_CALLS, 4))[0]:
        return "repli inutile"
    current = process.qword(SLOT)
    if current not in (ORIGINAL, CODE_ADDRESS):
        sys.exit(f"entrée 0x180 inattendue : {current:#x}")
    if process.read(STATE, 1)[0] == 2 or process.qword(ENTITY) == SERVER_NAVIGATION_ENTITY:
        return "repli déjà construit"
    place_code(process, CODE_ADDRESS, CODE)
    process.write(DATA_SLOT, struct.pack("<Q", data))
    process.write(STATE, b"\0")
    process.write(SLOT, struct.pack("<Q", CODE_ADDRESS))
    return f"repli posé (données {data:#x}, entité statique jamais détruite : ne pas changer de niveau)"


def main() -> None:
    pid, action = int(sys.argv[1]), sys.argv[2]
    process = Process(pid)
    if action == "etat":
        print(f"créateur {process.qword(CREATOR_SLOT):#x} ; {creator_state(process)} ; repli : entrée {process.qword(SLOT):#x}, état {process.read(STATE, 1)[0]}, données {process.qword(DATA_SLOT):#x} ; {navpower_state(process)}")
    elif action == "poser":
        place_creator(process)
        fallback = ""
        if len(sys.argv) > 3:
            data = int(sys.argv[3], 0)
            if not is_level_data(process, data):
                sys.exit(f"données {data:#x} invalides")
            fallback = " ; " + place_fallback(process, data)
        print(f"posé : créateur serveur -> {CREATOR_CODE_ADDRESS:#x} ; {creator_state(process)}{fallback} ; {navpower_state(process)}")
    elif action == "retirer":
        if process.qword(SLOT) == CODE_ADDRESS:
            process.write(SLOT, struct.pack("<Q", ORIGINAL))
        if process.qword(CREATOR_SLOT) == CREATOR_CODE_ADDRESS:
            process.write(CREATOR_SLOT, struct.pack("<Q", CREATOR_ORIGINAL))
        print(f"retiré : emplacements d'origine remis ; {creator_state(process)} ; {navpower_state(process)}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
