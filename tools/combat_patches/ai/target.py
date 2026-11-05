"""Usage : python3 -I target.py PID {poser [JOUEUR]|retirer|etat} : rend aux IA serveur la cible et les conditions retirées de l'exécutable client. Écrit le personnage serveur du player en 0x142b2abf0 (JOUEUR s'il est donné, 0 pour poser sans player : le code rend alors « pas de cible » et « faux » ; sinon cherché par player.py, pose refusée s'il est introuvable), pose le code target.s en 0x142b2ac00 et dévie sept entrées de tables : EntityProvider_AITarget (0x143acf790, évaluateur 0x148cc8d00 par le relais 0x140d1af20 qui rend 0 pour une entité serveur) vers un relais qui rend dans ce cas le player connecté le plus proche (nearest_player de player_list.s, posé d'abord ; repli : la case) ; BTConditional_RangeToTarget (0x143ad0240, « faux ») vers une comparaison de la distance de l'IA à ce player (positions entité+0x1b0, 2D sans +0x1b4 si Is2DRange, écart vertical +0x1b4 seul si HeightOnlyCheck +0x31, comme perception.s) au seuil Range selon Comparator ; BTConditional_LOSToTarget (0x143ad0058, table partagée avec HeardSound), BTConditional_AngleToTarget/AngleFromTarget (0x143ad0268) et BTConditional_TargetIsPlayer (0x143ad0080), tous « faux », vers 0x14036d060 (vrai) ; BoolProvider_WantsToAttack/WantsToFlee (table commune 0x143acf7c8, évaluateur 0x143acf7d8 « faux ») vers un code qui rend vrai pour WantsToAttack (données de type 0x145280710) seulement ; BTConditional_HasEnemies (0x143ad02b8, rend Inverse) vers « non Inverse » (0x142b29b00). « retirer » remet les valeurs d'origine (0x140d1af20, 0x140368b70 ×5, 0x140c7e840).

Repeated bootstrap installation preserves the verified perception successor and
its existing player pointer. Unknown entries are rejected before any writes.
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "health"))
sys.path.insert(0, HERE)
from health_mirror import Process
import player
import player_list

CODE_ADDRESS = 0x142B2AC00
HAS_ENEMIES_CODE = 0x142B29B00
HAS_ENEMIES_BYTES = bytes.fromhex("0fb6413483f001c3")
ALWAYS_TRUE = 0x14036D060
CODE = open(os.path.join(HERE, "target.bin"), "rb").read()
SLOTS = (("AITarget", 0x143ACF790, CODE_ADDRESS + 0x130, 0x140D1AF20), ("RangeToTarget", 0x143AD0240, CODE_ADDRESS, 0x140368B70),
         ("LOSToTarget", 0x143AD0058, ALWAYS_TRUE, 0x140368B70), ("HasEnemies", 0x143AD02B8, HAS_ENEMIES_CODE, 0x140C7E840),
         ("AngleToTarget", 0x143AD0268, ALWAYS_TRUE, 0x140368B70), ("TargetIsPlayer", 0x143AD0080, ALWAYS_TRUE, 0x140368B70),
         ("WantsToAttack", 0x143ACF7D8, CODE_ADDRESS + 0x170, 0x140368B70))


def main() -> None:
    pid, action = int(sys.argv[1]), sys.argv[2]
    process = Process(pid)

    def write(address: int, data: bytes) -> None:
        process.memory.seek(address)
        process.memory.write(data)

    current = {name: process.qword(slot) for name, slot, _, _ in SLOTS}
    if action == "etat":
        print(f"player {process.qword(player.PLAYER_SLOT):#x} ; " + " ; ".join(f"{name} {value:#x}" for name, value in current.items()))
    elif action == "poser":
        target = int(sys.argv[3], 0) if len(sys.argv) > 3 else player.find(process)[0]
        if len(sys.argv) <= 3 and not target:
            sys.exit("personnage serveur du player introuvable")
        # Perception is a verified successor to these bootstrap entries. On
        # another expedition, preserve it and its fallback pointers rather than
        # reject it (or clear the player slot on every failed retry).
        import perception
        successors = {name: perception.entry(index) for index, (name, _) in enumerate(perception.SLOTS)}
        retained = {name for name, value in current.items() if value == successors.get(name)}
        if retained:
            present = process.read(perception.CODE_ADDRESS, len(perception.CODE))
            begin = perception.PREVIOUS - perception.CODE_ADDRESS
            end = begin + 8 * len(perception.SLOTS)
            if present[:begin] + present[end:] != perception.CODE[:begin] + perception.CODE[end:]:
                sys.exit("code perception existant non vérifié : rien modifié")
        for name, slot, replacement, original in SLOTS:
            if current[name] not in (replacement, original) and name not in retained:
                sys.exit(f"entrée {name} inattendue : {current[name]:#x}")
        present = process.read(CODE_ADDRESS, len(CODE))
        has_enemies = process.read(HAS_ENEMIES_CODE, len(HAS_ENEMIES_BYTES))
        if present != CODE:
            if not all(byte in (0xCC, wanted) for byte, wanted in zip(present, CODE)):
                sys.exit("emplacement du code non vide")
        if has_enemies != HAS_ENEMIES_BYTES and set(has_enemies) != {0xCC}:
            sys.exit("emplacement HasEnemies non vide")
        player_list.install(process)
        if present != CODE:
            write(CODE_ADDRESS, CODE)
        if has_enemies != HAS_ENEMIES_BYTES:
            write(HAS_ENEMIES_CODE, HAS_ENEMIES_BYTES)
        if target or not retained:
            write(player.PLAYER_SLOT, struct.pack("<Q", target))
        for name, slot, replacement, original in SLOTS:
            if name not in retained and current[name] != replacement:
                write(slot, struct.pack("<Q", replacement))
        if retained:
            print("cibles déjà préparées : perception conservée ; case du joueur préservée" if not target else f"perception conservée ; joueur {target:#x}")
        else:
            print(f"posé : cible des IA = player {target:#x}" if target else "posé sans player : case 0x142b2abf0 à 0")
    elif action == "retirer":
        for name, slot, replacement, original in SLOTS:
            if current[name] == replacement:
                write(slot, struct.pack("<Q", original))
        print("retiré : entrées d'origine remises")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
