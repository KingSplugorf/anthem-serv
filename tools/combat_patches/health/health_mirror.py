"""Accès à la mémoire du jeu (/proc/PID/mem) et recherche d'objets par table virtuelle, partagés par les correctifs : Process.instances lit par blocs de 16 Mo répartis sur 4 fils (environ 10 s pour 9 Go). Constantes des composants de santé et des personnages serveur et client, position d'entité (ServerDylanCharacter +0x1b0, ClientDylanCharacter +0x1e0) pour l'appariement de health_follow.py. La recopie de la vie qui donnait son nom au fichier est remplacée par la réplication native (published_health.py)."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from process_memory import open_memory, read_at, writable_regions as process_regions

import os
import struct
from concurrent.futures import ThreadPoolExecutor

import numpy

SERVER_HEALTH = 0x143A88538
CLIENT_HEALTH = 0x143A8D310
SERVER_CHARACTER = 0x143A75900
CLIENT_CHARACTER = 0x143A6FF30
SERVER_POSITION = 0x1B0
CLIENT_POSITION = 0x1E0
MAX_DISTANCE = 6.0
CHUNK = 16 << 20
SCAN_THREADS = 4


class Process:
    def __init__(self, pid: int, writable: bool = True) -> None:
        self.pid = pid
        self.memory = open_memory(pid, "r+b" if writable else "rb")

    def read(self, address: int, size: int) -> bytes:
        if not 0x10000 <= address < 1 << 47:
            raise OSError(f"adresse invalide {address:#x}")
        self.memory.seek(address)
        data = self.memory.read(size)
        if len(data) != size:
            raise OSError(f"lecture incomplète {address:#x}")
        return data

    def qword(self, address: int) -> int:
        return struct.unpack("<Q", self.read(address, 8))[0]

    def float(self, address: int) -> float:
        return struct.unpack("<f", self.read(address, 4))[0]

    def write(self, address: int, data: bytes) -> None:
        if not 0x10000 <= address < 1 << 47:
            raise OSError(f"adresse invalide {address:#x}")
        self.memory.seek(address)
        self.memory.write(data)

    def write_float(self, address: int, value: float) -> None:
        self.write(address, struct.pack("<f", value))

    def regions(self) -> list[tuple[int, int]]:
        return [(start, end) for start, end in process_regions(self.pid) if start < 1 << 47]

    def instances(self, tables: list[int], regions: list[tuple[int, int]]) -> tuple[dict[int, list[int]], set[tuple[int, int]]]:
        wanted = numpy.array(tables, dtype="<u8")
        pieces = [(region, start, min(CHUNK, region[1] - start)) for region in regions for start in range(region[0], region[1], CHUNK)]

        def scan(share: list[tuple[tuple[int, int], int, int]]) -> list[tuple[tuple[int, int], int, int]]:
            memory = open_memory(self.pid, "rb")
            hits = []
            try:
                for region, start, size in share:
                    try:
                        data = read_at(memory, start, size)
                    except OSError:
                        continue
                    words = numpy.frombuffer(data, dtype="<u8", count=len(data) // 8)
                    hits.extend((region, start + 8 * int(index), int(words[index])) for index in numpy.flatnonzero(numpy.isin(words, wanted)))
            finally:
                memory.close()
            return hits

        found = {table: [] for table in tables}
        used = set()
        with ThreadPoolExecutor(SCAN_THREADS) as pool:
            for hits in pool.map(scan, [pieces[index::SCAN_THREADS] for index in range(SCAN_THREADS)]):
                for region, address, table in hits:
                    found[table].append(address)
                    used.add(region)
        for addresses in found.values():
            addresses.sort()
        return found, used

def entity_position(process: Process, component: int, character_table: int, offset: int):
    entity = process.qword(process.qword(component + 8))
    if process.qword(entity) != character_table:
        return None
    return struct.unpack("<fff", process.read(entity + offset, 12))

