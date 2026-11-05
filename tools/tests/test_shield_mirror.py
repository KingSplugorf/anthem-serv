import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('shield_mirror',Path(__file__).resolve().parents[1]/'combat_patches/health/health_follow.py')
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)


class Memory:
    def __init__(self): self.data,self.writes = {},[]
    def put(self,address,data): self.data.update((address+i,value) for i,value in enumerate(data))
    def read(self,address,size):
        try: return bytes(self.data[address+i] for i in range(size))
        except KeyError: raise OSError('Unmapped fixture memory')
    def qword(self,address): return struct.unpack('<Q',self.read(address,8))[0]
    def float(self,address): return struct.unpack('<f',self.read(address,4))[0]
    def write_float(self,address,value):
        self.writes.append((address,value)); self.put(address,struct.pack('<f',value))


class ShieldMirrorTests(unittest.TestCase):
    def fixture(self,client_shield=True):
        process = Memory()
        server,client,server_block,client_block = 0x100000,0x200000,0x300000,0x400000
        server_shield,client_component,server_state,client_state = 0x500000,0x600000,0x700000,0x800000
        for health,table,block,shield in ((server,mirror.SERVER_HEALTH,server_block,server_shield),
                (client,mirror.CLIENT_HEALTH,client_block,client_component)):
            process.put(health,bytes(0x40)); process.put(health,struct.pack('<QQ',table,block))
            process.put(block,bytes(0x100)); process.put(block+8,bytes([2]))
            process.put(block+0x20,struct.pack('<Q',health)); process.put(block+0x40,struct.pack('<Q',shield))
        for component,table,block,offset,state in ((server_shield,mirror.SERVER_SHIELD,server_block,0x50,server_state),
                (client_component,mirror.CLIENT_SHIELD,client_block,0x58,client_state)):
            process.put(component,bytes(0x80)); process.put(component,struct.pack('<QQ',table,block))
            process.put(component+offset,struct.pack('<Q',state)); process.put(state,bytes(16))
        process.put(server_state,struct.pack('<f',42)); process.put(client_state,struct.pack('<f',100))
        if not client_shield: process.put(client_block+0x40,bytes(8))
        return process,(server,client),(server_shield,client_component),(server_state,client_state)

    def test_late_client_shield_is_retried_instead_of_caching_absence(self):
        process,pair,components,states = self.fixture(client_shield=False)
        cache = {}
        mirror.mirror_shields(process,[pair],cache)
        self.assertEqual(process.writes,[])
        self.assertEqual(cache[pair],(components[0],None))
        process.put(0x400040,struct.pack('<Q',components[1]))
        mirror.mirror_shields(process,[pair],cache)
        self.assertEqual(process.writes,[(states[1],42),(states[1]+12,42)])

    def test_retired_cached_shield_never_receives_new_state(self):
        process,pair,components,states = self.fixture()
        cache = {pair:components}
        replacement,replacement_state = 0x900000,0xA00000
        process.put(replacement,process.read(components[1],0x80))
        process.put(replacement+0x58,struct.pack('<Q',replacement_state))
        process.put(replacement_state,bytes(16)); process.put(replacement_state,struct.pack('<f',100))
        process.put(0x400040,struct.pack('<Q',replacement))
        mirror.mirror_shields(process,[pair],cache)
        self.assertEqual(cache[pair],(components[0],replacement))
        self.assertEqual(process.float(states[1]),100)
        self.assertEqual(process.writes,[(replacement_state,42),(replacement_state+12,42)])

    def test_foreign_component_or_reused_health_pair_is_not_written(self):
        process,pair,components,_ = self.fixture()
        process.put(components[1]+8,struct.pack('<Q',0x300000))
        mirror.mirror_shields(process,[pair],{})
        self.assertEqual(process.writes,[])
        process.put(pair[1],bytes(8))
        cache = {pair:components}
        mirror.mirror_shields(process,[pair],cache)
        self.assertEqual(cache,{})
        self.assertEqual(process.writes,[])

    def test_invalid_server_values_are_rejected_and_nan_client_can_be_resynchronized(self):
        for value in (-1,float('nan'),float('inf')):
            with self.subTest(value=value):
                process,pair,_,states = self.fixture()
                process.put(states[0],struct.pack('<f',value))
                mirror.mirror_shields(process,[pair],{})
                self.assertEqual(process.writes,[])
        process,pair,_,states = self.fixture()
        process.put(states[1],struct.pack('<f',float('nan')))
        mirror.mirror_shields(process,[pair],{})
        self.assertEqual(process.writes,[(states[1],42),(states[1]+12,42)])
