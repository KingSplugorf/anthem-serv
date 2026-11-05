"""Usage : python3 -I creations.py PID {poser|retirer|etat} : journalise chaque ServerDylanSimpleHealthComponent et ClientDylanSimpleHealthComponent construit (fins des constructeurs 0x148ad5e52 et 0x140cbca84 déviées vers 0x142b28400) dans un anneau de 64 pointeurs en 0x153737c50, compteur en 0x153737c40 ; lu par health_follow.py."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from process_memory import open_memory

import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
CODE_ADDRESS = 0x142B28400
CODE = open(os.path.join(HERE, "creations.bin"), "rb").read()
HOOKS = {0x148AD5E52: (bytes.fromhex("4c89dc415ec3"), CODE_ADDRESS), 0x140CBCA84: (bytes.fromhex("4883c4505fc3"), CODE_ADDRESS + 7)}


def jump(site: int, target: int) -> bytes:
    return b"\xe9" + struct.pack("<i", target - (site + 5)) + b"\x90"


def main() -> None:
    pid, action = int(sys.argv[1]), sys.argv[2]
    memory = open_memory(pid, "r+b")

    def read(address: int, size: int) -> bytes:
        memory.seek(address)
        return memory.read(size)

    def write(address: int, data: bytes) -> None:
        memory.seek(address)
        memory.write(data)

    states = {site: ("dévié" if read(site, 6) == jump(site, target) else "origine" if read(site, 6) == original else "inattendu") for site, (original, target) in HOOKS.items()}
    if action == "etat":
        counter = struct.unpack("<I", read(0x153737C40, 4))[0]
        print(f"fins de constructeurs {', '.join(f'{site:#x}={state}' for site, state in states.items())} ; créations journalisées {counter}")
        return
    if action == "poser":
        if any(state == "inattendu" for state in states.values()):
            sys.exit(f"octets inattendus : {states}")
        code = read(CODE_ADDRESS, len(CODE))
        if code != CODE:
            if set(code) != {0xCC}:
                sys.exit("emplacement du code non vide")
            write(CODE_ADDRESS, CODE)
        for site, (original, target) in HOOKS.items():
            if states[site] == "origine":
                write(site, jump(site, target))
        print("posé")
    elif action == "retirer":
        for site, (original, target) in HOOKS.items():
            if states[site] == "dévié":
                write(site, original)
        print("retiré (code laissé en place)")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
