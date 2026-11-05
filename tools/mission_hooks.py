"""Mission hook behavior shared by GDB and the native Windows debugger."""
import json
import re
import struct
import time
from pathlib import Path

import hub_game

MISSION_CONNECT_STATE = 0x14A60F852
STARTUP_STATE_ENTRY = 0x148010F00
GATE_ENTRY = 0x148537DA0
STARTING_INFO_POINTER = 0x144A92578
ATTRIBUTES = ("playMode", "questId", "desiredDifficulty", "startPoint")
ATTRIBUTE_VALUE = re.compile(
    r'"(\w+)"\s*:\s*(?:générique\(\s*(?:"(\d+)"|(\d+))\s*\)|"(\d+)"|(\d+))'
    r'(?=\s*(?:[,}]|$))'
)


def parse_attributes(line):
    """Decode the numeric forms supported by Blaze's template_attributes."""
    values = {match[0]: int(next(value for value in match[1:] if value))
              for match in ATTRIBUTE_VALUE.findall(line)}
    if not all(name in values and 0 <= values[name] <= 0xffffffff for name in ATTRIBUTES):
        return None
    return tuple(values[name] for name in ATTRIBUTES)


class MissionHooks:
    def __init__(self, memory, pid, data=Path("data")):
        self.memory, self.pid, self.data = memory, pid, Path(data)
        self.launched = None

    def qword(self, address):
        return struct.unpack("<Q", self.memory.read(address, 8))[0]

    def starting_info(self):
        pointer = self.qword(STARTING_INFO_POINTER)
        return pointer, struct.unpack("<IIII", self.memory.read(pointer, 16))

    def log(self, text):
        with (self.data / f"active_mission_{self.pid}.txt").open("a", encoding="utf-8") as output:
            output.write(f"{time.strftime('%H:%M:%S')} {text}\n")

    def last_attributes(self):
        latest = None
        with (self.data / "logs/blaze.log").open(errors="replace", encoding="utf-8") as source:
            for line in source:
                if line.startswith(("SCNA = ", "TMPA = ")):
                    latest = line
        return parse_attributes(latest) if latest is not None else None

    def use_root_level(self, client):
        pointer = self.qword(client + 0x390)
        level = self.memory.read(pointer, 64).split(b"\0")[0]
        root = b"levels/Root/Root"
        if level.lower() == root.lower():
            return
        if len(level) != len(root):
            self.log(f"  niveau {level!r} laissé : longueur différente de {root!r}")
            return
        self.memory.write(pointer, root)
        self.log(f"  niveau du client {pointer:#x} {level!r} -> {root!r}")

    def use_quest_start_point(self, quest):
        try:
            with (self.data / f"quest_points_{self.pid}.json").open(encoding="utf-8") as source:
                table = json.load(source)
        except (OSError, ValueError) as error:
            self.log(f"  point de lancement non posé : {error}")
            return
        point = table["quetes"].get(str(quest))
        if point is None:
            self.log(f"  quête {quest} sans point de lancement connu : point par défaut laissé")
            return
        for component in table["composants"]:
            self.memory.write(component + 0x30, struct.pack("<QQ", point["chaine"], point["chaine"]))
        self.log(f"  point par défaut -> {point['point']} ({point['nom']})")

    def connect(self, registers):
        state = registers["rdx"] & 0xffffffff
        # Never carry a preceding mission's state into a join or shared hub.
        self.launched = None
        if state != 5:
            self.log(f"connexion à un serveur (état {state:#x}) : serveur interne non démarré, rien n'est posé")
            return
        game = self.qword(registers["rsi"] + 0x50)
        if hub_game.is_hub_game(game):
            self.log(f"hub partagé : partie {game}, hub rechargé en hôte pair, rien n'est posé")
            return
        self.launched = self.last_attributes()
        self.log(f"lancement de mission : GameStartingInfo à poser {self.launched}")
        if self.launched is None:
            self.log("  lancement non préparé : aucun SCNA/TMPA complet et valide dans data/logs/blaze.log ; niveau et GameStartingInfo laissés inchangés")
            return
        self.use_root_level(registers["rcx"])
        self.use_quest_start_point(self.launched[1])
        pointer, values = self.starting_info()
        self.memory.write(pointer, struct.pack("<IIII", *self.launched))
        self.log(f"  GameStartingInfo {pointer:#x} {values} -> {self.launched}")

    def startup(self, registers):
        if self.launched is None:
            return
        startup, state = registers["rcx"], registers["rdx"] & 0xffffffff
        values = struct.unpack("<IIII", self.memory.read(startup + 0x18, 16))
        started = self.memory.read(startup + 0x31, 1)[0]
        self.memory.write(startup + 0x18, struct.pack("<IIII", *self.launched))
        self.memory.write(startup + 0x31, b"\x01")
        self.log(f"état de démarrage {state} : ServerDylanStartup {startup:#x} GameStartingInfo {values} démarrée {started}")

    def gate(self, registers):
        if self.launched is not None:
            pointer, values = self.starting_info()
            if values != self.launched:
                self.memory.write(pointer, struct.pack("<IIII", *self.launched))
                self.log(f"porte : GameStartingInfo {pointer:#x} {values} -> {self.launched}")

    def playmode_write(self, registers):
        if self.launched is not None:
            pointer, values = self.starting_info()
            stack = struct.unpack("<64Q", self.memory.read(registers["rsp"], 512))
            returns = " ".join(f"{word:#x}" for word in stack if 0x140001000 <= word < 0x150000000)
            self.log(f"écriture de PlayMode : {values} après {registers['rip']:#x}, pile {returns}")
            if values != self.launched:
                self.memory.write(pointer, struct.pack("<IIII", *self.launched))
