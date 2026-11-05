"""Usage : python3 -I damage.py PID {poser|retirer|etat|bonus X|invulnerable oui|non} : rend aux composants de santé serveur leur ApplyDamage. Tous les dégâts serveur (impact 0x140c86ef0, BWCSMAction_DamageCone 0x1408fcd10, BWCSMAction_ApplyDamage 0x1408fb780, 0x140c01870, 0x140c39550, 0x140c5c390) passent par 0x140c15020(interface santé, &résultat, ServerDylanDamageInfo), qui appelle le vf10 de l'interface (composant+0xa8, table 0x143a87fb0, partagée par le joueur et les IA) puis diffuse DylanHealthComponentOnDamageMessage (0x143a821b8) avec le résultat aux composants de l'entité. Côté serveur, ce vf10 (0x140beef50 → 0x1488dc810) ne rend qu'un résultat nul. Le code damage.s (0x142b2d400) le remplace comme le vf10 client (0x140cba0c0 → 0x148b365b0) : il appelle le calcul commun intact, vf48 de l'interface (0x140c162f0 → 0x1489308c0 : équipe, immunité, montant [info+0xd0], résistances, point faible, bouclier par l'interface [iface+0xb8] et son setter natif, dégâts de vie plafonnés à la vie en résultat+4), puis applique ce que la partie serveur retirée faisait : vie − résultat+4 par set_health de published_health.py (à poser d'abord : points bornés, réplication, CurrentHealth), sauf si l'état [+0x158] n'est pas vivant ou si les données sont Immortal (+0x12f). À 0 : état 1 et DylanHealthCompDyingMessage (0x6b3a91e0, table 0x143a8b648) ; pour une IA, puis état 2, DylanHealthCompDeathMessage (0xfae11fc1, table 0x143a8b580), réplication de l'état (masque 8), mort native 0x1415d0950 (OnKilled) et ServerCharacterKilledMessage (0x1408fc7b0, compte de mission) ; pour le joueur, l'agonie seule (à terre), état répliqué. Deux reprises de l'init serveur retirée : l'interface bouclier [composant+0x160] (= iface+0xb8), nulle sur tous les composants relevés, est rattachée au ServerRegeneratingShieldComponent de l'entité (table 0x143a76ae0, interface en +0x30) ; un bouclier chargé (maximum [iface bouclier+0x28] > 0) est déclaré actif (octet [[iface bouclier+0x20]+9], composant+0x159, exigé par le test d'applicabilité 0x140c25b60 → 0x148943e10 et jamais posé par le serveur, faute d'init) et répliqué en entier (masque 7 du sous-objet réseau composant+0x110 : valeur, délai, maximum et réglages) ; le bit 0x20 de [info+0x16a] est posé (l'impact le pose déjà) pour passer le contrôle d'équipe 0x140d32920, toutes les équipes Frostbite valant 2 sur ce serveur (l'hostilité est décidée en amont par perception.py). Bonus de dégâts du joueur : flottant en 0x1537377e8, lu par impacts.s (server_health.py) qui multiplie l'ancien montant des impacts venus du client qui ne sont pas des tirs de l'arme active (missiles du lanceur, mêlée et explosions des sections de server_sections.py, tirs dont le dégât de l'arme n'a pu être lu : repli) avant 0x140c86ef0, donc [info+0xd0] ; les tirs d'arme du joueur reçoivent le dégât lu dans l'instance vars de l'arme active, sans bonus ; les tirs d'IA de shots.s, marqués rcx = 1, n'y sont pas soumis, ni les dégâts reçus par le joueur. « poser » le met à 1 s'il est nul, « bonus X » le règle à chaud (1 : aucun bonus). « invulnerable oui » (octet 0x1537377ec) fait ignorer tout dégât reçu par le joueur (composant de santé qui n'est pas ServerDylanSimpleHealthComponent), pour essayer les IA ; les IA restent blessées normalement ; « invulnerable non » le rétablit."""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from health_mirror import Process

CODE_ADDRESS = 0x142B2D400
CODE = open(os.path.join(HERE, "damage.bin"), "rb").read()
SLOT = 0x143A87FB0 + 0x10
ORIGINAL = 0x140BEEF50
COMPUTE_DAMAGE = 0x140C162F0
HEALTH_CODE_ADDRESS = 0x142B2E400
PLAYER_BONUS, DEFAULT_BONUS = 0x1537377E8, 1.0
INVULNERABLE_PLAYER = 0x1537377EC
HEALTH_CODE = open(os.path.join(HERE, "published_health.bin"), "rb").read()


def main() -> None:
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    process, action = Process(int(sys.argv[1])), sys.argv[2]
    current = process.qword(SLOT)
    if action == "etat":
        print(f"vf10 interface santé -> {current:#x} ({'ApplyDamage' if current == CODE_ADDRESS else 'origine' if current == ORIGINAL else 'inattendu'})"
              f" ; bonus du joueur {process.float(PLAYER_BONUS):g} ; joueur invulnérable {'oui' if process.read(INVULNERABLE_PLAYER, 1)[0] else 'non'}")
    elif action == "poser":
        if current not in (ORIGINAL, CODE_ADDRESS, COMPUTE_DAMAGE):
            sys.exit(f"vf10 inattendu {current:#x}")
        if process.read(HEALTH_CODE_ADDRESS, len(HEALTH_CODE)) != HEALTH_CODE:
            sys.exit("published_health.py pas encore posé (set_health absent)")
        present = process.read(CODE_ADDRESS, len(CODE))
        if present != CODE:
            if set(present) != {0xCC} and current == CODE_ADDRESS:
                sys.exit("ancienne version du code en service : retirer d'abord")
            if set(present) != {0xCC} and current != CODE_ADDRESS and not present.startswith(CODE[:8]):
                sys.exit("emplacement du code non vide")
            process.write(CODE_ADDRESS, CODE)
        process.write(SLOT, struct.pack("<Q", CODE_ADDRESS))
        if process.float(PLAYER_BONUS) == 0:
            process.write_float(PLAYER_BONUS, DEFAULT_BONUS)
        print(f"posé : ApplyDamage serveur -> {CODE_ADDRESS:#x}, bonus du joueur {process.float(PLAYER_BONUS):g}")
    elif action == "bonus":
        if len(sys.argv) != 4 or float(sys.argv[3]) <= 0:
            sys.exit(__doc__)
        process.write_float(PLAYER_BONUS, float(sys.argv[3]))
        print(f"bonus du joueur {process.float(PLAYER_BONUS):g}")
    elif action == "invulnerable":
        if len(sys.argv) != 4 or sys.argv[3] not in ("oui", "non"):
            sys.exit(__doc__)
        process.write(INVULNERABLE_PLAYER, b"\x01" if sys.argv[3] == "oui" else b"\x00")
        print(f"joueur invulnérable : {sys.argv[3]}")
    elif action == "retirer":
        process.write(SLOT, struct.pack("<Q", ORIGINAL))
        print("retiré")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
