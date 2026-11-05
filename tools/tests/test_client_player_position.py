import contextlib
from concurrent.futures import Future
import io
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'combat_patches/ai'))
import player
import player_position as follower


class Process:
    def __init__(self): self.memory, self.data, self.writes = self, {}, []
    def put(self, address, data): self.data.update((address+i,byte) for i,byte in enumerate(data))
    def read(self, address, size):
        try: return bytes(self.data[address+i] for i in range(size))
        except KeyError: raise OSError('unmapped fixture memory')
    def qword(self, address): return struct.unpack('<Q',self.read(address,8))[0]
    def seek(self, address): self.position=address
    def write(self, *args):
        address,data = (self.position,args[0]) if len(args)==1 else args
        self.writes.append((address,bytes(data)))
        self.put(address,data)


def client(process, base=0x100000):
    health,block,entity,shield = base,base+0x10000,base+0x20000,base+0x30000
    for address,value in ((health,player.CLIENT_HEALTH),(health+8,block),(block,entity),
            (entity,player.CLIENT_CHARACTER),(entity+0x58,block),
            (block+0x20,health),(block+0x40,shield),
            (shield,player.CLIENT_SHIELD),(shield+8,block),(shield+0x58,base+0x40000)):
        process.put(address,struct.pack('<Q',value))
    process.put(block+8,bytes([2,4,0]))
    process.put(block+0xC0,struct.pack('<3f',10,20,30))
    return health,shield,entity,block


class ClientPositionTests(unittest.TestCase):
    def test_stale_vtable_hits_are_excluded_before_unique_copy_selection(self):
        process=Process()
        health,_,_,block=client(process)
        stale=0x900000
        process.put(stale,struct.pack('<QQ',player.CLIENT_HEALTH,block))
        self.assertEqual(player.client_character(process,stale),0)
        self.assertEqual(player.client_copies(process,[stale,health,health]),(health,0x140000))

    def test_two_valid_client_players_remain_ambiguous(self):
        process=Process()
        first=client(process)[0]
        second=client(process,0x200000)[0]
        self.assertEqual(player.client_copies(process,[first,second]),(0,0))

    def test_reused_client_actor_is_not_selected(self):
        process=Process()
        health,_,entity,_=client(process)
        process.put(entity,struct.pack('<Q',0x145000000))
        self.assertEqual(player.client_copies(process,[health]),(0,0))

    def test_discovery_uses_and_closes_a_separate_read_only_process(self):
        probe=Mock()
        for error in (False,True):
            with patch.object(follower,'Process',return_value=probe) as constructor, \
                 patch.object(follower.player,'find',side_effect=OSError('transition') if error else None,
                              return_value=(1,2,3)):
                if error:
                    with self.assertRaises(OSError): follower.discover_client_copies(123)
                else: self.assertEqual(follower.discover_client_copies(123),(1,2,3))
            constructor.assert_called_once_with(123,writable=False)
        self.assertEqual(probe.memory.close.call_count,2)

    def test_refresh_rejects_an_old_server_character_or_remote_player(self):
        process=Process()
        health,shield,_,_=client(process)
        process.put(player.PLAYER_SLOT,struct.pack('<Q',0x600000))
        with patch.object(follower,'local',return_value=False):
            self.assertFalse(follower.accept_client_copies(process,0x600000,(0x600000,health,shield)))
        with patch.object(follower,'local',return_value=True):
            self.assertFalse(follower.accept_client_copies(process,0x600000,(0x700000,health,shield)))
        self.assertEqual(process.writes,[])

    def test_missing_client_copy_is_rediscovered_while_server_character_remains_valid(self):
        process=Process()
        health,shield,_,_=client(process)
        server,aiming,block=0x600000,0x700000,0x800000
        for address,value in ((player.PLAYER_SLOT,server),(server,player.SERVER_CHARACTER),
                (server+0x7A8,aiming),(aiming,follower.AIMING_COMPONENT),
                (aiming+8,block),(server+0x58,block),(block+0x20,aiming)):
            process.put(address,struct.pack('<Q',value))
        process.put(block+8,bytes([1,4,0]))
        process.put(player.CLIENT_HEALTH_SLOT,bytes(8))
        process.put(aiming+0x150,struct.pack('<3f',100,200,300))
        pool=Mock()
        future=Future(); future.set_result((server,health,shield)); pool.submit.return_value=future
        with patch.object(follower.time,'monotonic',return_value=0), \
             patch.object(follower.time,'sleep'), patch.object(follower,'local',return_value=True), \
             patch.object(follower,'os',SimpleNamespace(name='nt')), \
             patch.object(follower,'process_alive',side_effect=[True,True,False]), \
             contextlib.redirect_stdout(io.StringIO()):
            follower.follow_positions(process,123,100,pool)
        pool.submit.assert_called_once_with(follower.discover_client_copies,123)
        self.assertEqual(process.qword(player.CLIENT_HEALTH_SLOT),health)
        self.assertEqual(struct.unpack('<3f',process.read(server+0x1B0,12)),(10,20,30))
        self.assertEqual(struct.unpack('<3f',process.read(block+0xC0,12)),(10,20,30))
        self.assertEqual(process.qword(player.PLAYER_SLOT),server)

    def test_native_windows_position_is_preserved_when_only_an_aim_origin_is_available(self):
        process=Process()
        server,aiming,block=0x600000,0x700000,0x800000
        for address,value in ((player.PLAYER_SLOT,server),(server,player.SERVER_CHARACTER),
                (server+0x7A8,aiming),(aiming,follower.AIMING_COMPONENT),
                (aiming+8,block),(server+0x58,block),(block+0x20,aiming)):
            process.put(address,struct.pack('<Q',value))
        process.put(block+8,bytes([1,4,0]))
        process.put(player.CLIENT_HEALTH_SLOT,bytes(8))
        process.put(aiming+0x150,struct.pack('<3f',100,200,300))
        process.put(server+0x1B0,struct.pack('<3f',1,2,3))
        process.put(block+0xC0,struct.pack('<3f',1,2,3))
        pool=Mock(); pool.submit.return_value=Future()
        with patch.object(follower.time,'monotonic',return_value=0), \
             patch.object(follower.time,'sleep'), patch.object(follower,'local',return_value=True), \
             patch.object(follower,'os',SimpleNamespace(name='nt')), \
             patch.object(follower,'process_alive',side_effect=[True,False]), \
             contextlib.redirect_stdout(io.StringIO()):
            follower.follow_positions(process,123,100,pool)
        self.assertEqual(process.writes,[])
        self.assertEqual(struct.unpack('<3f',process.read(server+0x1B0,12)),(1,2,3))
