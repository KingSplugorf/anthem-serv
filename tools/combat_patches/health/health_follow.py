"""Usage : python3 -I health_follow.py PID [--duree SECONDES] : recopie le bouclier des ennemis et suit les objets destructibles, que le serveur ne réplique pas. La vie et la mort des personnages passent par l'ApplyDamage natif et la réplication native (damage.py, published_health.py). Bouclier : tient la liste des composants de santé serveur et clients (inventaire complet au démarrage puis toutes les 10 min, créations lues dans l'anneau de creations.py), apparie chaque serveur à sa copie cliente et recopie, toutes les 0,5 s, la valeur du bouclier serveur ([[ServerRegeneratingShieldComponent+0x50]]) dans la copie cliente ([[ClientRegeneratingShieldComponent+0x58]] +0 et +0xc). Appariement : d'abord par ordre de création (l'anneau de creations.py enregistre chaque ServerDylanSimpleHealthComponent immédiatement suivi de sa copie cliente, mêmes données +0x10 ; les 64 dernières créations sont relues au démarrage). Les paires établies sont gardées tant que les deux composants vivent ; une adresse revue dans l'anneau est un nouvel objet et ses anciennes paires sont oubliées. À défaut, l'appariement se fait par position (mêmes données +0x10, entité la plus proche à moins de 6 : ServerDylanCharacter +0x1b0 / ClientDylanCharacter +0x1e0). Objets destructibles touchés (anneau 0x153737f80 de impacts.s, server_health.py) : apparie chaque ServerCombatPlaceableHealthComponent à sa copie cliente (mêmes données +0x10, plus petite distance entre les triplets de flottants des 0x400 premiers octets des deux entités ; nouvel inventaire au plus toutes les 15 s si la copie manque) et, à chaque changement de vie ou d'état serveur, remplit une case {composant client, vie, état} de 0x153737f00 lue par death.py. Note une fois chaque table d'entité touchée sans composant de santé connu (anneau 0x153737fd0)."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from process_memory import alive as process_alive

import math
import os
import struct
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from health_mirror import (CLIENT_CHARACTER, CLIENT_HEALTH, CLIENT_POSITION, MAX_DISTANCE, SERVER_CHARACTER, SERVER_HEALTH, SERVER_POSITION, Process,
                        entity_position)

RING_COUNTER = 0x153737C40
RING_ENTRIES = 0x153737C50
RING_SIZE = 64
FULL_INVENTORY_SECONDS = 600
SERVER_SHIELD = 0x143A76AE0
CLIENT_SHIELD = 0x143A71690
SERVER_PLACEABLE = 0x143A740A0
CLIENT_PLACEABLE = 0x143A24FF0
SERVER_PLACEABLE_ENTITY = 0x143A743C8
CLIENT_PLACEABLE_ENTITY = 0x143A251C8
PLACEABLE_SCAN = 0x400
PLACEABLE_MAX_DISTANCE = 0.5
PLACEABLE_RETRY_SECONDS = 15.0
PLACEABLE_RING_COUNTER = 0x153737F80
PLACEABLE_RING = 0x153737F90
PLACEABLE_RING_SIZE = 8
PLACEABLE_SLOTS = 0x153737F00
PLACEABLE_SLOT_COUNT = 8
UNHANDLED_COUNTER = 0x153737FD0
UNHANDLED_RING = 0x153737FD8
UNHANDLED_RING_SIZE = 5


def live(process: Process, component: int, table: int) -> bool:
    try:
        if process.qword(component) != table:
            return False
        components = process.qword(component + 8)
        count = process.read(components + 8, 1)[0]
        return any(process.qword(components + 0x20 + index * 0x20) == component for index in range(count))
    except OSError:
        return False


def ring_entries(process: Process, last: int) -> tuple[list[int], int, bool]:
    counter = process.read(RING_COUNTER, 4)
    current = int.from_bytes(counter, "little")
    if current == last:
        return [], last, False
    overflow = current - last > RING_SIZE
    start = max(last, current - RING_SIZE)
    pointers = [process.qword(RING_ENTRIES + (index % RING_SIZE) * 8) for index in range(start, current)]
    return pointers, current, overflow


def compute_pairs(process: Process, servers: set[int], clients: set[int], previous: list[tuple[int, int]],
                  created: dict[int, int]) -> list[tuple[int, int]]:
    kept, kept_servers, kept_clients = [], set(), set()
    for server, client in [*created.items(), *previous]:
        if server in kept_servers or client in kept_clients:
            continue
        if server in servers and client in clients and live(process, server, SERVER_HEALTH) and live(process, client, CLIENT_HEALTH):
            kept.append((server, client))
            kept_servers.add(server)
            kept_clients.add(client)
    client_info = []
    for client in list(clients):
        try:
            if process.qword(client) != CLIENT_HEALTH:
                clients.discard(client)
                continue
            if not live(process, client, CLIENT_HEALTH):
                continue
            position = entity_position(process, client, CLIENT_CHARACTER, CLIENT_POSITION)
            if position and client not in kept_clients:
                client_info.append((client, process.qword(client + 0x10), position))
        except OSError:
            clients.discard(client)
    candidates_by_server = []
    for server in list(servers):
        if server in kept_servers:
            continue
        try:
            if process.qword(server) != SERVER_HEALTH:
                servers.discard(server)
                continue
            if not live(process, server, SERVER_HEALTH):
                continue
            data = process.qword(server + 0x10)
            position = entity_position(process, server, SERVER_CHARACTER, SERVER_POSITION)
        except OSError:
            servers.discard(server)
            continue
        if not position:
            continue
        candidates_by_server += [(math.dist(position, other), server, client) for client, client_data, other in client_info if client_data == data]
    result = list(kept)
    for distance, server, client in sorted(candidates_by_server):
        if distance < MAX_DISTANCE and server not in kept_servers and client not in kept_clients:
            result.append((server, client))
            kept_servers.add(server)
            kept_clients.add(client)
    return result


def inventory(pid: int, servers: set[int], clients: set[int], client_placeables: set[int], lock: threading.Lock) -> None:
    process = Process(pid)
    found, _ = process.instances([SERVER_HEALTH, CLIENT_HEALTH, CLIENT_PLACEABLE], process.regions())
    with lock:
        servers.update(found[SERVER_HEALTH])
        clients.update(found[CLIENT_HEALTH])
        client_placeables.clear()
        client_placeables.update(found[CLIENT_PLACEABLE])
    print(f"{time.strftime('%H:%M:%S')} inventaire : {len(found[SERVER_HEALTH])} serveur, {len(found[CLIENT_HEALTH])} client, "
          f"{len(found[CLIENT_PLACEABLE])} objets clients", flush=True)


def entity_component(process: Process, component: int, table: int) -> int | None:
    components = process.qword(component + 8)
    count = process.read(components + 8, 1)[0]
    for index in range(count):
        other = process.qword(components + 0x20 + index * 0x20)
        if other and process.qword(other) == table:
            return other
    return None


def mirror_shields(process: Process, pairs: list[tuple[int, int]], shields: dict) -> None:
    for server, client in pairs:
        try:
            if (server, client) not in shields:
                server_shield = entity_component(process, server, SERVER_SHIELD)
                client_shield = entity_component(process, client, CLIENT_SHIELD)
                shields[(server, client)] = (server_shield, client_shield)
            server_shield, client_shield = shields[(server, client)]
            if not server_shield or not client_shield:
                continue
            server_state, client_state = process.qword(server_shield + 0x50), process.qword(client_shield + 0x58)
            if not server_state or not client_state:
                continue
            value = process.float(server_state)
            if value >= 0.0 and abs(process.float(client_state) - value) > 0.01:
                process.write_float(client_state, value)
                process.write_float(client_state + 0xC, value)
        except OSError:
            continue


def ring_since(process: Process, counter_address: int, entries: int, size: int, last: int) -> tuple[list[int], int]:
    current = int.from_bytes(process.read(counter_address, 4), "little")
    start = max(last, current - size)
    return [process.qword(entries + (index % size) * 8) for index in range(start, current)], current


def placeable_points(process: Process, component: int, table: int) -> list[tuple[float, float, float]]:
    entity = process.qword(process.qword(component + 8))
    if process.qword(entity) != table:
        return []
    raw = process.read(entity, PLACEABLE_SCAN)
    points = (struct.unpack_from("<fff", raw, offset) for offset in range(0, PLACEABLE_SCAN - 12, 4))
    return [point for point in points if all(1.0 < abs(value) < 100000.0 for value in point)]


def pair_placeable(process: Process, server: int, client_placeables: set[int]) -> int | None:
    data = process.qword(server + 0x10)
    here = placeable_points(process, server, SERVER_PLACEABLE_ENTITY)
    best = None
    for client in list(client_placeables):
        try:
            if not live(process, client, CLIENT_PLACEABLE) or process.qword(client + 0x10) != data:
                continue
            there = placeable_points(process, client, CLIENT_PLACEABLE_ENTITY)
            distance = min((math.dist(a, b) for a in here for b in there), default=math.inf)
            if distance < PLACEABLE_MAX_DISTANCE and (best is None or distance < best[0]):
                best = (distance, client)
        except OSError:
            continue
    return best[1] if best else None


def follow_placeables(process: Process, watched: dict[int, list], client_placeables: set[int]) -> bool:
    unpaired = False
    for server, follow in list(watched.items()):
        try:
            if not live(process, server, SERVER_PLACEABLE):
                del watched[server]
                continue
            if follow[0] is None:
                if time.monotonic() < follow[2]:
                    unpaired = True
                    continue
                follow[0] = pair_placeable(process, server, client_placeables)
                if follow[0] is None:
                    if not follow[2]:
                        print(f"{time.strftime('%H:%M:%S')} objet {server:#x} sans copie cliente connue : nouvel inventaire", flush=True)
                    follow[2] = time.monotonic() + PLACEABLE_RETRY_SECONDS
                    unpaired = True
                    continue
                print(f"{time.strftime('%H:%M:%S')} objet {server:#x} -> {follow[0]:#x}", flush=True)
            value = (process.float(server + 0x20), process.read(server + 0x100, 1)[0] & 3)
            if value == follow[1]:
                continue
            for slot in range(PLACEABLE_SLOT_COUNT):
                address = PLACEABLE_SLOTS + slot * 16
                if process.qword(address) == 0:
                    process.memory.seek(address + 8)
                    process.memory.write(struct.pack("<fI", *value))
                    process.memory.seek(address)
                    process.memory.write(struct.pack("<Q", follow[0]))
                    follow[1] = value
                    if value[1] == 2:
                        print(f"{time.strftime('%H:%M:%S')} objet détruit {server:#x} -> {follow[0]:#x}", flush=True)
                    break
        except OSError:
            watched.pop(server, None)
    return unpaired


def main() -> None:
    pid = int(sys.argv[1])
    duration = float(sys.argv[sys.argv.index("--duree") + 1]) if "--duree" in sys.argv else 3600.0
    deadline = time.monotonic() + duration
    process = Process(pid)
    servers: set[int] = set()
    clients: set[int] = set()
    lock = threading.Lock()
    last = max(0, int.from_bytes(process.read(RING_COUNTER, 4), "little") - RING_SIZE)
    created: dict[int, int] = {}
    previous_server = None
    client_placeables: set[int] = set()
    watched: dict[int, list] = {}
    placeable_last = int.from_bytes(process.read(PLACEABLE_RING_COUNTER, 4), "little")
    unhandled_last = int.from_bytes(process.read(UNHANDLED_COUNTER, 4), "little")
    unhandled_seen: set[int] = set()
    last_forced_inventory = 0.0
    shields: dict = {}
    inventory_thread = threading.Thread(target=inventory, args=(pid, servers, clients, client_placeables, lock), daemon=True)
    inventory_thread.start()
    next_inventory = time.monotonic() + FULL_INVENTORY_SECONDS
    next_pairing = 0.0
    current: list[tuple[int, int]] = []
    while time.monotonic() < deadline and process_alive(pid):
        try:
            pointers, last, overflow = ring_entries(process, last)
        except OSError:
            pointers, overflow = [], False
        if overflow:
            print(f"{time.strftime('%H:%M:%S')} anneau débordé : inventaire complet", flush=True)
            next_inventory = 0.0
        with lock:
            reborn = set(pointers)
            if reborn:
                created = {server: client for server, client in created.items() if server not in reborn and client not in reborn}
                current = [(server, client) for server, client in current if server not in reborn and client not in reborn]
                shields = {pair: value for pair, value in shields.items() if not reborn & set(pair)}
            for pointer in pointers:
                try:
                    table = process.qword(pointer)
                    data = process.qword(pointer + 0x10)
                except OSError:
                    continue
                if table == SERVER_HEALTH:
                    servers.add(pointer)
                    previous_server = (pointer, data)
                elif table == CLIENT_HEALTH:
                    clients.add(pointer)
                    if previous_server and previous_server[1] == data:
                        created[previous_server[0]] = pointer
                    previous_server = None
        if time.monotonic() >= next_inventory and not inventory_thread.is_alive():
            inventory_thread = threading.Thread(target=inventory, args=(pid, servers, clients, client_placeables, lock), daemon=True)
            inventory_thread.start()
            next_inventory = time.monotonic() + FULL_INVENTORY_SECONDS
        try:
            hits, placeable_last = ring_since(process, PLACEABLE_RING_COUNTER, PLACEABLE_RING, PLACEABLE_RING_SIZE, placeable_last)
            for server in hits:
                watched.setdefault(server, [None, None, 0.0])
            tables, unhandled_last = ring_since(process, UNHANDLED_COUNTER, UNHANDLED_RING, UNHANDLED_RING_SIZE, unhandled_last)
            for table in set(tables) - unhandled_seen:
                unhandled_seen.add(table)
                print(f"{time.strftime('%H:%M:%S')} impact sans composant de santé connu : entité de table {table:#x}", flush=True)
        except OSError:
            pass
        with lock:
            unpaired = follow_placeables(process, watched, client_placeables)
        if unpaired and time.monotonic() >= last_forced_inventory + PLACEABLE_RETRY_SECONDS and not inventory_thread.is_alive():
            last_forced_inventory = time.monotonic()
            next_inventory = 0.0
        if time.monotonic() >= next_pairing:
            with lock:
                fresh = compute_pairs(process, servers, clients, current, created)
            if set(fresh) != set(current):
                print(f"{time.strftime('%H:%M:%S')} {len(fresh)} paires ({len(servers)} serveur, {len(clients)} client)", flush=True)
            current = fresh
            mirror_shields(process, current, shields)
            next_pairing = time.monotonic() + 0.5
        time.sleep(0.05)


if __name__ == "__main__":
    main()
