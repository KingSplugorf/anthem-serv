"""Usage : python3 -I tools/patch_finrachaos.py PID [--appliquer] : liste, puis avec --appliquer corrige dans le jeu lancé l'appel du hook de destructeur de FINRACHAOS.dll (0x1416f8000) qui saute sur le pointeur de libération du moteur au lieu de le lire (mov rax, imm64 devient mov rax, [imm64])."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from process_memory import open_memory, module_base as process_module_base

import struct

MODULE = "FINRACHAOS.dll"
POINTER_SLOTS = [0x1442F1AE8]
CALL_SITES = [0x3189]
MOVE_IMMEDIATE = b"\x48\xb8"
MOVE_FROM_ADDRESS = b"\x48\xa1"


def module_base(pid: int) -> int:
    return process_module_base(pid, MODULE)


def main() -> None:
    pid, apply = int(sys.argv[1]), "--appliquer" in sys.argv[2:]
    patterns = [MOVE_IMMEDIATE + struct.pack("<Q", slot) for slot in POINTER_SLOTS]
    corrected = [MOVE_FROM_ADDRESS + struct.pack("<Q", slot) for slot in POINTER_SLOTS]
    with open_memory(pid, "r+b" if apply else "rb") as memory:
        base = module_base(pid)
        sites = []
        for offset in CALL_SITES:
            memory.seek(base + offset)
            sites.append((base + offset, memory.read(10)))
        if all(found in corrected for _, found in sites):
            print(f"{len(sites)} sites déjà corrigés")
            return
        unexpected = [f"{address:#x}" for address, found in sites if found not in patterns]
        if unexpected:
            sys.exit(f"octets inattendus en {', '.join(unexpected)} : rien n'est modifié")
        for address, pattern in sites:
            print(f"{address:#x} mov rax, {struct.unpack('<Q', pattern[2:])[0]:#x}")
        if apply:
            for address, _ in sites:
                memory.seek(address)
                memory.write(MOVE_FROM_ADDRESS)
            print(f"{len(sites)} sites corrigés")


if __name__ == "__main__":
    main()
