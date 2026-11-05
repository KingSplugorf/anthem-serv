"""Usage : python3 -I tools/default_point.py PID POINT : fait pointer DefaultTransitionPoint (+0x30) et DefaultTransitionStartPointName (+0x38) du TransitionLevelDescriptionComponent du niveau racine (DefaultServerType 1 en +0x40) vers la chaîne du point de départ POINT déjà présente dans sa table de points (entrée retrouvée par son RegionHash), pour que le serveur interne démarre à ce point sans allocation. python3 -I tools/default_point.py PID --quetes : relève ces composants et, pour chaque quête de rootquestinfocollection, l'adresse de la chaîne de son point de lancement (TransitionPoint de son DylanEcoQuestMapMarkerSetting, data/game/globals/enumerations/ecoquestmapmarkersettingsglobalasset.json), puis écrit data/quest_points_PID.json (adresses propres à ce processus), que tools/gdb_active_mission.gdb lit au lancement d'une mission pour démarrer au point de la quête."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from process_memory import open_memory, writable_regions as process_regions

import json
import struct

import numpy

DESCRIPTION = "data/game/root_description.json"
QUESTS = "data/game/levels/root/rootquestinfocollection.json"
MARKERS = "data/game/globals/enumerations/ecoquestmapmarkersettingsglobalasset.json"
DEFAULT_POINT, DEFAULT_START_POINT_NAME, DEFAULT_SERVER_TYPE = 0x30, 0x38, 0x40
ENTRY_SERVER_TYPE, ENTRY_REGION_HASH = 16, 20
CURRENT_DEFAULT = "Enclave_HQ"
MAXIMUM_REGION = 1 << 30
READ_PIECE, PIECE_OVERLAP = 16 << 20, 64


def transition_states(node):
    if isinstance(node, dict):
        if "ServerType" in node and "StartPointName" in node:
            yield node
        for value in node.values():
            yield from transition_states(value)
    elif isinstance(node, list):
        for value in node:
            yield from transition_states(value)


def quest_points():
    markers = json.load(open(MARKERS))
    points = {instance["guid"]: instance["champs"].get("TransitionPoint") for instance in markers["objets"] if instance["guid"]}
    found = {}
    for instance in json.load(open(QUESTS))["objets"]:
        fields = instance["champs"]
        if "QuestId" not in fields:
            continue
        for marker in fields.get("QuestMapMarkerSettings") or []:
            if marker.get("import", [None])[0] == markers["fichier"] and points.get(marker["import"][1]):
                found[fields["QuestId"]] = (fields["QuestName"], points[marker["import"][1]])
                break
    return found


def writable_regions(pid):
    for start, end in process_regions(pid):
        if end - start <= MAXIMUM_REGION:
            for piece in range(start, end, READ_PIECE):
                yield piece, min(end, piece + READ_PIECE + PIECE_OVERLAP)


def text_at(memory, pointer):
    try:
        memory.seek(pointer)
        return memory.read(64).split(b"\0")[0].decode("latin1")
    except (OSError, ValueError, OverflowError):
        return None


def locate(pid, memory, wanted):
    regions = {}
    for state in transition_states(json.load(open(DESCRIPTION))):
        if state["StartPointName"] in wanted:
            regions.setdefault(state["RegionHash"], set()).add(state["StartPointName"])
    chunks = []
    for start, end in writable_regions(pid):
        try:
            memory.seek(start)
            chunks.append((start, memory.read(end - start)))
        except OSError:
            continue
    point_strings, default_strings = {}, []
    needle = CURRENT_DEFAULT.encode() + b"\0"
    hashes = numpy.array(sorted(regions), dtype=numpy.uint32)
    for start, chunk in chunks:
        position = chunk.find(needle)
        while position >= 0:
            default_strings.append(start + position)
            position = chunk.find(needle, position + 1)
        words = numpy.frombuffer(chunk[: len(chunk) // 4 * 4], dtype="<u4")
        for index in numpy.nonzero(numpy.isin(words, hashes))[0]:
            entry = int(index) * 4 - ENTRY_REGION_HASH
            if entry >= 0 and entry % 8 == 0:
                pointer = struct.unpack_from("<Q", chunk, entry)[0]
                name = text_at(memory, pointer)
                if name in regions[int(words[index])]:
                    point_strings[name] = pointer
    targets = numpy.array(sorted(default_strings), dtype=numpy.uint64)
    components = []
    for start, chunk in chunks:
        qwords = numpy.frombuffer(chunk[: len(chunk) // 8 * 8], dtype="<u8")
        for index in numpy.nonzero(numpy.isin(qwords, targets))[0]:
            component = int(index) * 8 - DEFAULT_POINT
            if component < 0 or component + DEFAULT_SERVER_TYPE + 4 > len(chunk):
                continue
            start_name = struct.unpack_from("<Q", chunk, component + DEFAULT_START_POINT_NAME)[0]
            server_type = struct.unpack_from("<I", chunk, component + DEFAULT_SERVER_TYPE)[0]
            if start_name in default_strings and server_type == 1:
                components.append(start + component)
    return components, point_strings


def record_quest_points(pid, memory):
    quests = quest_points()
    components, point_strings = locate(pid, memory, {point for _, point in quests.values()})
    if not components:
        sys.exit("composant du niveau racine introuvable")
    table = {str(quest): {"nom": name, "point": point, "chaine": point_strings[point]}
             for quest, (name, point) in quests.items() if point in point_strings}
    with open(f"data/quest_points_{pid}.json", "w") as output:
        json.dump({"composants": components, "quetes": table}, output, indent=1)
    print(f"{len(components)} composants, {len(table)} quêtes sur {len(quests)} avec leur point de lancement")


def main():
    pid, point = int(sys.argv[1]), sys.argv[2]
    memory = open_memory(pid, "r+b")
    if point == "--quetes":
        record_quest_points(pid, memory)
        return
    components, point_strings = locate(pid, memory, {point})
    if point not in point_strings:
        sys.exit(f"chaîne du point {point} introuvable")
    for component in components:
        memory.seek(component + DEFAULT_POINT)
        memory.write(struct.pack("<QQ", point_strings[point], point_strings[point]))
    print(f"{len(components)} composants redirigés vers {point} ({point_strings[point]:#x})")


if __name__ == "__main__":
    main()
