import gc
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import weakref
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import default_point


class TrackedChunk(bytearray):
    pass


class Memory:
    def __init__(self, blocks):
        self.blocks=blocks
        self.position=0
        self.references=[]
        self.peak_chunks=0
        self.scanned=[]

    def seek(self,address): self.position=address

    def read(self,size):
        for start,data in self.blocks.items():
            offset=self.position-start
            if 0<=offset and offset+size<=len(data):
                self.position+=size
                if size==len(data):
                    chunk=TrackedChunk(data)
                    self.references.append(weakref.ref(chunk))
                    self.peak_chunks=max(self.peak_chunks,sum(ref() is not None for ref in self.references))
                    self.scanned.append(start)
                    return chunk
                return bytes(data[offset:offset+size])
        raise OSError('unmapped fixture memory')


class DefaultPointScanTests(unittest.TestCase):
    def test_cross_chunk_references_are_found_without_retaining_all_game_memory(self):
        blocks={0x10000+index*0x1000:bytearray(256) for index in range(12)}
        regions=[(start,start+len(data)) for start,data in blocks.items()]
        default_address=0x18080
        point_address=0x14080
        blocks[0x18000][0x80:0x80+len(b'Enclave_HQ\0')]=b'Enclave_HQ\0'
        blocks[0x14000][0x80:0x80+len(b'Mission_Start\0')]=b'Mission_Start\0'
        struct.pack_into('<Q',blocks[0x10000],0x30,point_address)
        struct.pack_into('<I',blocks[0x10000],0x30+20,0x12345678)
        component=0x12020
        struct.pack_into('<QQI',blocks[0x12000],0x20+0x30,default_address,default_address,1)
        memory=Memory(blocks)
        with tempfile.TemporaryDirectory() as folder:
            description=Path(folder)/'description.json'
            description.write_text(json.dumps({'StartPointName':'Mission_Start','ServerType':1,'RegionHash':0x12345678}))
            with patch.object(default_point,'DESCRIPTION',str(description)), \
                 patch.object(default_point,'writable_regions',return_value=iter(regions)):
                components,points=default_point.locate(123,memory,{'Mission_Start'})
        self.assertEqual(components,[component])
        self.assertEqual(points,{'Mission_Start':point_address})
        self.assertLessEqual(memory.peak_chunks,3)
        self.assertEqual(len(memory.scanned),len(blocks)*2)
        gc.collect()
        self.assertFalse(any(ref() is not None for ref in memory.references))


if __name__=='__main__': unittest.main()
