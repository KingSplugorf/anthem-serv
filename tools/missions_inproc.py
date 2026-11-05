"""Usage : python3 -I tools/missions_inproc.py PID [--verifier] : passe en mémoire le type de serveur des points de départ de transition de Dedicated (1) à InProc (0), pour que le client héberge lui-même les missions comme Fort Tarsis ; les entrées sont retrouvées par leur RegionHash et le nom de leur point de départ (data/game/root_description.json)."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from process_memory import open_memory, writable_regions as process_regions

import json
import struct

import numpy

DESCRIPTION = "data/game/root_description.json"
SERVER_TYPE_IN_PROC, SERVER_TYPE_DEDICATED = 0, 1
START_POINT_NAME, SERVER_TYPE = 0, 16
MAXIMUM_NAME = 96
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


def writable_regions(pid):
    for start, end in process_regions(pid):
        if end - start <= MAXIMUM_REGION:
            for piece in range(start, end, READ_PIECE):
                yield piece, min(end, piece + READ_PIECE + PIECE_OVERLAP)


def name_at(memory, pointer):
    try:
        memory.seek(pointer)
        return memory.read(MAXIMUM_NAME).split(b"\0")[0].decode("latin1")
    except (OSError, ValueError, OverflowError):
        return None


def main():
    pid, check_only = int(sys.argv[1]), "--verifier" in sys.argv
    states = list(transition_states(json.load(open(DESCRIPTION))))
    names = {state["StartPointName"] for state in states if state["ServerType"] == SERVER_TYPE_DEDICATED}
    hashes = numpy.array(sorted({state["RegionHash"] for state in states}), dtype=numpy.uint32)
    memory = open_memory(pid, "r+b")
    found = {SERVER_TYPE_IN_PROC: 0, SERVER_TYPE_DEDICATED: 0}
    for start, end in writable_regions(pid):
        try:
            memory.seek(start)
            chunk = memory.read(end - start)
        except OSError:
            continue
        words = numpy.frombuffer(chunk[: len(chunk) // 4 * 4], dtype="<u4")
        for index in numpy.nonzero(numpy.isin(words, hashes))[0]:
            type_offset = int(index) * 4 - 4
            entry = type_offset - SERVER_TYPE
            if entry < 0 or entry % 8 or words[index - 1] not in (SERVER_TYPE_IN_PROC, SERVER_TYPE_DEDICATED):
                continue
            pointer = struct.unpack_from("<Q", chunk, entry + START_POINT_NAME)[0]
            if name_at(memory, pointer) not in names:
                continue
            server_type = int(words[index - 1])
            found[server_type] += 1
            if server_type == SERVER_TYPE_DEDICATED and not check_only:
                memory.seek(start + type_offset)
                memory.write(struct.pack("<I", SERVER_TYPE_IN_PROC))
    action = "trouvées" if check_only else "passées en InProc"
    print(f"entrées Dedicated {action} : {found[SERVER_TYPE_DEDICATED]}, déjà InProc : {found[SERVER_TYPE_IN_PROC]}")


if __name__ == "__main__":
    main()
