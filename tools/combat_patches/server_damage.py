"""Usage : python3 -I tools/combat_patches/server_damage.py PID {poser|retirer|etat} : pose un rappel serveur d'impacts (absent de l'exécutable client) en 0x144759e68/0x144759e70 ; pour chaque impact reçu du client, il applique à la cible (structure +0x38 -> entité) le montant +0xa0 via 0x140c86ef0, après vérification des pointeurs et du montant (0 < x < 100000)."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from process_memory import open_memory

import os
import struct

CODE_ADDRESS = 0x142B27F00
CALLBACK_OBJECT = 0x144759E68
CALLBACK_FUNCTION = 0x144759E70
CODE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "callback", "callback.bin"), "rb").read()
HEALTH_CALLBACK = 0x142B2C800
HEALTH_CODE = (Path(__file__).resolve().parent / 'health' / 'impacts.bin').read_bytes()


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

    obj, fn = qword(CALLBACK_OBJECT), qword(CALLBACK_FUNCTION)
    code = read(CODE_ADDRESS, len(CODE))
    code_state = "posé" if code == CODE else "absent" if set(code) == {0xCC} else "inattendu"
    if action == "etat":
        print(f"rappel serveur objet={obj:#x} fonction={fn:#x} ; code {code_state}")
    elif action == "poser":
        # server_health upgrades this bootstrap callback. A new expedition
        # must preserve that verified successor rather than reject or downgrade it.
        if fn == HEALTH_CALLBACK:
            if obj not in (CODE_ADDRESS, HEALTH_CALLBACK) or read(HEALTH_CALLBACK, len(HEALTH_CODE)) != HEALTH_CODE:
                sys.exit("rappel santé existant non vérifié : rien modifié")
            print("rappel santé déjà posé : conservé pour cette expédition")
            return
        if (obj, fn) not in ((0, 0), (CODE_ADDRESS, CODE_ADDRESS)):
            sys.exit(f"rappel serveur déjà occupé : objet={obj:#x} fonction={fn:#x}")
        if code_state == "inattendu":
            sys.exit("emplacement du code non vide")
        if code_state == "absent":
            write(CODE_ADDRESS, CODE)
        write(CALLBACK_FUNCTION, struct.pack("<Q", CODE_ADDRESS))
        write(CALLBACK_OBJECT, struct.pack("<Q", CODE_ADDRESS))
        print(f"posé : {CALLBACK_OBJECT:#x}/{CALLBACK_FUNCTION:#x} -> {CODE_ADDRESS:#x}")
    elif action == "retirer":
        write(CALLBACK_OBJECT, bytes(8))
        write(CALLBACK_FUNCTION, bytes(8))
        print("retiré : rappel serveur remis à 0 (code laissé en place, inactif)")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
