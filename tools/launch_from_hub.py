"""Usage : python3 -I tools/launch_from_hub.py PID [--pair] : dans le traitement « se connecter à un serveur » du client (0x14a60f760, ClientJoinMultiplayer), fait démarrer le serveur interne au lieu de la connexion à un serveur. Par défaut (solo), sans condition, valeurs de ClientStartSingleplayer (0x1416c22a5) : écoute locale [client+0x413] = 1, mode [client+0x90] = 0, donc backend OnlineBackend_Local, et état StartServer (5) au lieu de ConnectToServer (0xf). Avec --pair, posé à l'identique sur l'hôte et sur le jeu qui rejoint : 0x14a60f7ba à 0x14a60f7d0 et 0x14a60f84a sont déviés vers peer_launch.s (0x142b36000, zone int3 de .xcode ; as puis objcopy -O binary -j .text), qui ne démarre le serveur interne, avec les valeurs de ClientStartMultiplayer d'un niveau coop (0x1416c1ef5 : [client+0x413] = 0, [client+0x414] = 1, mode 1, donc backend OnlineBackend_Peer et gestionnaire composite mémoire + DirtySDK, docs/etude-multijoueur/transport.md, puis StartServer), que si le joueur local est hôte de topologie de la partie Blaze visée : backend de gestion de partie [client+0xc8] (interface +0x80 de DylanClientGameManagementBackend, table 0x143a444b8), sa partie [backend+0xb0] (interface [jeu+0x388] de table 0x143ca3c28), identifiant [jeu+8] égal au premier mot de l'adresse du message (+0x50, posée par 0x14815d7b0), point local [jeu+0x768] non nul et égal au point de topologie [jeu+0x6a0] (méthode +0x68, 0x14d30fad0) ; sinon valeurs d'origine (mode 3, ConnectToServer vers l'hôte). --pair lève aussi le refus du maillage d'un hôte de topologie dédié avec voix (0x1428737e7, jne -> jmp), fait poster ClientJoinMultiplayer par un hôte de topologie à la fin de jonction de son joueur (0x14815d812, jne -> nop), et fait envoyer par son serveur Live (DylanBigsData, [0x1446c8130]) les données Live (LiveSendSleeperContentBools, LiveSendConfigData) aux clients distants malgré son client local (0x140ab3048 et 0x140ab3c37, jne -> nop), ainsi que le flux des réglages variables de chaque ServerConnection (objet de table 0x143a4bce8, écriture 0x140a72750 : états 3 et 4 refusés avec un client local, 0x140a727d9 et 0x140a72a77 jne -> nop ; tableau de réglages vide sans fiche de fin, 0x140a72a95 je -> nop), seul moyen de poser le bit 2 du client Live distant (0x140d562b0) ; ces sites sont sans effet sur un jeu qui n'est pas hôte de topologie ou n'a pas de serveur interne."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from process_memory import open_memory

import os
import struct

LISTEN_FLAGS, MODE, STATE = 0x14A60F7C1, 0x14A60F7CD, 0x14A60F84B
JOIN_VALUES, JOIN_STATE = 0x14A60F7BA, 0x14A60F84A
JOIN_VALUES_ORIGINAL = bytes.fromhex("66c787130400000000488d5650c7879000000003000000")
JOIN_STATE_ORIGINAL = bytes.fromhex("ba0f000000")
PAIR_CODE_ADDRESS, PAIR_STATE_ENTRY = 0x142B36000, 0x60
PAIR_CODE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "peer_launch.bin"), "rb").read()
DEDICATED_MESH_VOICE_REFUSAL = 0x1428737E7
TOPOLOGY_HOST_JOIN_SKIP = 0x14815D812
LIVE_CONNECTION_SKIP, LIVE_SEND_SKIP = 0x140AB3048, 0x140AB3C37
TUNINGS_HEADER_SKIP, TUNINGS_VALUES_SKIP, TUNINGS_EMPTY_SKIP = 0x140A727D9, 0x140A72A77, 0x140A72A95


def jump(source, target, size):
    return (b"\xe9" + struct.pack("<i", target - (source + 5))).ljust(size, b"\xcc")


def patches(peer):
    if not peer:
        return [
            (LISTEN_FLAGS, bytes.fromhex("0000"), bytes.fromhex("0100")),
            (MODE, bytes.fromhex("03"), bytes.fromhex("00")),
            (STATE, bytes.fromhex("0f"), bytes.fromhex("05")),
        ]
    return [
        (PAIR_CODE_ADDRESS, b"\xcc" * len(PAIR_CODE), PAIR_CODE),
        (JOIN_VALUES, JOIN_VALUES_ORIGINAL, jump(JOIN_VALUES, PAIR_CODE_ADDRESS, len(JOIN_VALUES_ORIGINAL))),
        (JOIN_STATE, JOIN_STATE_ORIGINAL, jump(JOIN_STATE, PAIR_CODE_ADDRESS + PAIR_STATE_ENTRY, len(JOIN_STATE_ORIGINAL))),
        (DEDICATED_MESH_VOICE_REFUSAL, bytes.fromhex("75"), bytes.fromhex("eb")),
        (TOPOLOGY_HOST_JOIN_SKIP, bytes.fromhex("0f8509010000"), bytes.fromhex("660f1f440000")),
        (LIVE_CONNECTION_SKIP, bytes.fromhex("0f858d010000"), bytes.fromhex("660f1f440000")),
        (LIVE_SEND_SKIP, bytes.fromhex("0f85e6000000"), bytes.fromhex("660f1f440000")),
        (TUNINGS_HEADER_SKIP, bytes.fromhex("0f8529020000"), bytes.fromhex("660f1f440000")),
        (TUNINGS_VALUES_SKIP, bytes.fromhex("0f8587000000"), bytes.fromhex("660f1f440000")),
        (TUNINGS_EMPTY_SKIP, bytes.fromhex("746d"), bytes.fromhex("6690")),
    ]


def apply(memory, chosen):
    found = []
    for address, original, _ in chosen:
        memory.seek(address)
        found.append(memory.read(len(original)))
    if all(current == patched for current, (_, _, patched) in zip(found, chosen)):
        return "déjà appliqué"
    unexpected = [f"{address:#x}" for current, (address, original, _) in zip(found, chosen) if current != original]
    if unexpected:
        sys.exit(f"octets inattendus en {', '.join(unexpected)} : rien n'est modifié")
    for address, _, patched in chosen:
        memory.seek(address)
        memory.write(patched)
    return "appliqué"


def main():
    pid = int(sys.argv[1])
    peer = "--pair" in sys.argv[2:]
    with open_memory(pid, "r+b") as memory:
        outcome = apply(memory, patches(peer))
    print(f"lancement de mission redirigé vers le serveur interne ({'pair, si hôte de topologie' if peer else 'solo'}) : {outcome}")


if __name__ == "__main__":
    main()
