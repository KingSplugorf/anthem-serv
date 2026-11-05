"""Usage : python3 -I server_sections.py PID {poser|retirer|etat} : pose les rappels serveur des sections A et B du flux d'impacts (0x140b3cb00), dont les gestionnaires d'origine (0x140a67b30 pour A ; 0x148901220 pour B, qui n'applique un coup que s'il retrouve l'explosion créée côté serveur, sinon le met en attente) ne font aucun dégât sur le serveur interne : A (mêlée, rappel [0x14475a4c8]/[0x14475a4d0], enregistrement +0x20 = entité touchée) et B (explosion, rappel [0x14475a4d8]/[0x14475a4e0], enregistrement +0x00 = poignée dont [0] = entité touchée + 8). Les rappels d'origine sont remplacés et gardés en 0x142b29ac0 pour « retirer » ; la pose est refusée tant que le serveur ne les a pas enregistrés (fonction nulle). Le code sections2.s (0x142b2a200) construit un impact ordinaire (+0x38 poignée, +0x40 tireur nul, +0xa0 montant fixe 6000) et appelle le rappel d'impacts courant [0x144759e70] (code de server_health.py, qui doit être posé) avec rcx = 2, marque des sections : impacts.s ne leur applique pas le dégât de l'arme active et garde l'ancien calcul (bonus du joueur). Entrée de l'explosion en +0x80. Une ancienne version du code en service (explosion en +0x57) se retire d'abord, puis se remplace."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from process_memory import open_memory

import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
CODE_ADDRESS = 0x142B2A200
OLDER_CODE = 0x142B29A00
EXPLOSION_OFFSET = 0x80
CODE_SPAN = 0x100
CALLBACK_FUNCTION = 0x144759E70
CALLBACKS = {"mêlée (A)": (0x14475A4C8, 0x14475A4D0, CODE_ADDRESS), "explosion (B)": (0x14475A4D8, 0x14475A4E0, CODE_ADDRESS + EXPLOSION_OFFSET)}
OLDER = {"mêlée (A)": (OLDER_CODE, OLDER_CODE), "explosion (B)": (OLDER_CODE, OLDER_CODE + 0x57)}
SAVED = 0x142B29AC0
CODE = open(os.path.join(HERE, "sections2.bin"), "rb").read()


def main() -> None:
    pid, action = int(sys.argv[1]), sys.argv[2]
    memory = open_memory(pid, "r+b")

    def read(address: int, size: int) -> bytes:
        memory.seek(address)
        return memory.read(size)

    def qword(address: int) -> int:
        return struct.unpack("<Q", read(address, 8))[0]

    def write(address: int, data: bytes) -> None:
        memory.seek(address)
        memory.write(data)

    current = {name: (qword(obj), qword(fn)) for name, (obj, fn, _) in CALLBACKS.items()}

    def in_service(callbacks: dict) -> bool:
        return any(CODE_ADDRESS <= fn < CODE_ADDRESS + CODE_SPAN for _, fn in callbacks.values())
    if action == "etat":
        print(" ; ".join(f"{name} objet={obj:#x} fonction={fn:#x}" for name, (obj, fn) in current.items()))
    elif action == "poser":
        if not qword(CALLBACK_FUNCTION):
            sys.exit("rappel d'impacts absent : poser server_health.py d'abord")
        if any(not fn for name, (obj, fn) in current.items() if current[name] != (CODE_ADDRESS, CALLBACKS[name][2])):
            sys.exit(f"rappels d'origine pas encore enregistrés par le serveur : {current}")
        code = read(CODE_ADDRESS, len(CODE))
        if code != CODE:
            if set(code) != {0xCC} and in_service(current):
                sys.exit("ancienne version du code en service : retirer d'abord")
            if set(code) != {0xCC} and not code.startswith(CODE[:16]):
                sys.exit("emplacement du code non vide")
            write(CODE_ADDRESS, CODE)
        for index, (name, (obj, fn, target)) in enumerate(CALLBACKS.items()):
            if current[name] == (CODE_ADDRESS, target):
                continue
            if current[name] != OLDER[name]:
                write(SAVED + index * 16, struct.pack("<QQ", *current[name]))
            write(fn, struct.pack("<Q", target))
            write(obj, struct.pack("<Q", CODE_ADDRESS))
        print(f"posé : mêlée et explosion -> {CODE_ADDRESS:#x} (origine gardée : {current})")
    elif action == "retirer":
        for index, (name, (obj, fn, target)) in enumerate(CALLBACKS.items()):
            if current[name] != OLDER[name] and not in_service({name: current[name]}):
                continue
            saved_obj, saved_fn = struct.unpack("<QQ", read(SAVED + index * 16, 16))
            write(fn, struct.pack("<Q", saved_fn))
            write(obj, struct.pack("<Q", saved_obj))
        print("retiré : rappels d'origine remis")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
