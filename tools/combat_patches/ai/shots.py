"""Usage : python3 -I shots.py PID {poser|retirer|etat|soigner} : « soigner » relève le player et remet sa vie à son maximum par la demande lue à chaque image par published_health.py (octet 0x1537377e4), ou à défaut, après 2 s, par écriture directe sans réplication ; fait toucher par les tirs des IA leur cible. Le serveur ne crée pas les projectiles de ses armes (0x148c949d0 : pour une arme serveur, seul le transtypage 0x1408b0e80 subsiste, en 0x148c94b4d), donc aucun tir d'IA n'atteint sa cible. Cet appel est dévié vers le code shots.s (0x142b2b000) : si l'arme ([comportement+0x10], interface de table 0x143a5c468 placée en +0x280 d'un ServerActionGearEntity de table 0x143a5bf40, les deux tables vérifiées avant toute autre lecture ; 0x143a3f2b0 est la table de leur copie cliente) appartient à une IA (propriétaire [[entité+0x1b8]]-8, ServerDylanCharacter dont [+0x2b8] est nul), le tir vise la cible perçue du tireur, rendue par cible_de (publiée par perception.py en 0x153737608 ; elle n'est jamais une entité amie ou neutre du tireur), ou, sans perception posée, le player connecté le plus proche du tireur (nearest_player de player_list.s, posé d'abord) ; aucune cible, aucun dégât. Joueur ou IA, la cible reçoit un impact ordinaire (poignée entité+8 en +0x38, montant en +0xa0) : le dégât que le jeu a évalué pour l'arme, lu sans rien évaluer comme le fait l'explosion 0x140bfca70 pour son DamageInfo : comportement de table DylanFireActionBehavior_Projectile 0x143a9dfd0 ou _ForceWall 0x143a9ef78 (seuls à tirer par 0x148c949d0 ; _Buckshot et _Multishot ont leur propre tir), DirectImpactDamage [[comportement+0x28]+0xf0], son Damage : fournisseur [+0x40] → repli, sans formule [+0x38] → DefaultValue [+0x48], sinon évaluateur de la formule dans l'arbre du propriétaire de statistiques [comportement+0x18] (posé par vf40 0x148c92200, formules inscrites par vf88 0x148caa2a0 → 0x1408f0760 ; en-tête +0x50, racine +0x60, [nœud] si clé [+0x20] < formule, sinon [nœud+8], évaluateur [+0x28], 64 pas au plus), lu par son vf30 avec dl = 1 (valeur avec bonus, bornée par la formule) ; un dégât non positif ou un maillon manquant donne le repli 100. Compteurs en 0x1537376e0 (tirs évalués, replis, dernier dégât évalué, dernière cause de repli), affichés par « etat ». Le montant est passé au rappel d'impacts courant [0x144759e70] avec rcx = 1 (tir d'IA, exclu du bonus de dégâts du player) (impacts.s de server_health.py, puis ApplyDamage natif de damage.py : bouclier, vie, mort). La case 0x142b2abf0 écrite par target.py (à poser d'abord) ne sert plus que de repli à la liste des joueurs. Touché assuré, sans ligne de vue ni distance."""

import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "health"))
sys.path.insert(0, HERE)
from health_mirror import Process
import player
import player_list

CODE_ADDRESS = 0x142B2B000
OLDER_CODE = 0x142B2AE00
SITE = 0x148C94B4D
ORIGINAL_TARGET = 0x1408B0E80
CODE = open(os.path.join(HERE, "shots.bin"), "rb").read()
HEAL_REQUEST = 0x1537377E4
COUNTERS = 0x1537376E0
REASON_BEHAVIOUR, REASON_PROVIDER, REASON_NO_OWNER, REASON_NOT_EVALUATED, REASON_NOT_POSITIVE = 1, 2, 3, 4, 5
REASONS = {0: "aucun", REASON_BEHAVIOUR: "comportement ou données illisibles", REASON_PROVIDER: "dégât donné par un fournisseur",
           REASON_NO_OWNER: "pas de propriétaire de statistiques", REASON_NOT_EVALUATED: "formule non évaluée pour l'arme",
           REASON_NOT_POSITIVE: "dégât nul ou négatif"}


def call(target: int) -> bytes:
    return b"\xe8" + struct.pack("<i", target - (SITE + 5))


def main() -> None:
    pid, action = int(sys.argv[1]), sys.argv[2]
    process = Process(pid)
    write = process.write
    site = process.read(SITE, 5)
    state = "dévié" if site == call(CODE_ADDRESS) else "origine" if site in (call(ORIGINAL_TARGET), call(OLDER_CODE)) else "inattendu"
    if action == "etat":
        print(f"appel {SITE:#x} {state} ; player {process.qword(player.PLAYER_SLOT):#x}")
        evaluated, fallbacks, last, reason = struct.unpack("<IIfI", process.read(COUNTERS, 16))
        print(f"tirs évalués {evaluated}, dernier dégât {last:g} ; replis à 100 : {fallbacks}, dernière cause {REASONS.get(reason, reason)}")
    elif action == "poser":
        if state == "inattendu":
            sys.exit(f"octets inattendus {site.hex()}")
        if process.qword(player.PLAYER_SLOT) == 0xCCCCCCCCCCCCCCCC:
            sys.exit("case du player jamais écrite : poser target.py d'abord")
        player_list.install(process)
        present = process.read(CODE_ADDRESS, len(CODE))
        if present != CODE:
            if set(present) != {0xCC} and process.read(SITE, 5) == call(CODE_ADDRESS):
                sys.exit("ancienne version du code en service : retirer d'abord")
            write(CODE_ADDRESS, CODE)
        write(SITE, call(CODE_ADDRESS))
        print("posé : tirs d'IA -> impact sur leur cible")
    elif action == "soigner":
        process.write(HEAL_REQUEST, b"\x01")
        time.sleep(2)
        if process.read(HEAL_REQUEST, 1) == b"\x00":
            print("player relevé, vie au maximum")
            return
        process.write(HEAL_REQUEST, b"\x00")
        character = process.qword(player.PLAYER_SLOT)
        components = process.qword(character + 0x58)
        for index in range(process.read(components + 8, 1)[0]):
            component = process.qword(components + 0x20 + index * 0x20)
            if component and process.qword(component) == 0x143A88050:
                process.write(component + 0x20, process.read(component + 0x24, 4))
                process.write(component + 0x158, bytes([process.read(component + 0x158, 1)[0] & 0xFC]))
        print("demande non lue : vie serveur du player remise à son maximum par écriture directe, sans réplication")
    elif action == "retirer":
        write(SITE, call(ORIGINAL_TARGET))
        print("retiré")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
