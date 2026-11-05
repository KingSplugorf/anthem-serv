"""Usage : python3 -I death.py PID {poser|retirer|etat} : dévie le relais client 0x14096b9a0 (jmp 0x1480e8810, mise à jour d'une arme cliente, fil client, à chaque image) vers le code mort5 (0x142b29800), qui vide les 8 cases {composant, vie, état} de 0x153737f00 et appelle pour chacune 0x1408b8730(ClientCombatPlaceableHealthComponent vivant, vie, état) (barre, OnDamaged, destruction à l'état 2), maximum mis à 1 s'il n'a jamais été posé ; les cases sont remplies par health_follow.py. Les objets destructibles n'ont pas de réplication serveur active ; la vie et la mort des personnages passent par la réplication native (published_health.py)."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from process_memory import open_memory

import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
STUB = 0x14096B9A0
ORIGINAL_TARGET = 0x1480E8810
CODE_ADDRESS = 0x142B29800
OLDER_CODES = (0x142B28500, 0x142B28E00)
CODE = open(os.path.join(HERE, "death5.bin"), "rb").read()


def jump(target: int) -> bytes:
    return b"\xe9" + struct.pack("<i", target - (STUB + 5))


def main() -> None:
    pid, action = int(sys.argv[1]), sys.argv[2]
    memory = open_memory(pid, "r+b")

    def read(address: int, size: int) -> bytes:
        memory.seek(address)
        return memory.read(size)

    def write(address: int, data: bytes) -> None:
        memory.seek(address)
        memory.write(data)

    stub = read(STUB, 5)
    state = "dévié" if stub == jump(CODE_ADDRESS) else "origine" if stub in [jump(ORIGINAL_TARGET)] + [jump(older) for older in OLDER_CODES] else "inattendu"
    if action == "etat":
        print(f"relais {STUB:#x} {stub.hex()} ({state})")
    elif action == "poser":
        if state == "inattendu":
            sys.exit(f"relais inattendu {stub.hex()}")
        code = read(CODE_ADDRESS, len(CODE))
        if code != CODE:
            if set(code) != {0xCC}:
                sys.exit("emplacement du code non vide")
            write(CODE_ADDRESS, CODE)
        write(STUB, jump(CODE_ADDRESS))
        print(f"posé : relais {STUB:#x} -> {CODE_ADDRESS:#x}")
    elif action == "retirer":
        write(STUB, jump(ORIGINAL_TARGET))
        print("retiré")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
