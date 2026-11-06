"""Opt-in bridge-only shield/resistance experiment: PID {poser|retirer|etat}.

Does not change native environmental damage or force shield values. Restart the
session after updating other patch blobs. Requires the supported generic routine
and the exact current server-health impact callback. Not a hit-part armor fix.
"""
import os
import struct
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from health_mirror import Process
import server_health

SITE, RESUME = 0x140C87088, 0x140C8709E
CAVE, END = 0x142B2CE00, 0x142B2D400
ORIGINAL = bytes.fromhex('0fb685da0000000c21f30f1175400c108885da000000')
NATIVE_PROLOGUE = bytes.fromhex('488bc455574156488da8a8fdffff4881ec40030000')
GENERIC = 0x140C86EF0
CALL = bytes.fromhex('48b8f06ec84001000000ffd0')
BLOB = Path(__file__).with_suffix('.bin').read_bytes()
assert len(BLOB) <= END-CAVE
assert server_health.CODE.count(CALL) == 1
RETURN = server_health.CODE_ADDRESS + server_health.CODE.index(CALL) + len(CALL)
CODE = BLOB[:-16] + struct.pack('<QQ',RETURN,RESUME)
PATCH = b'\xe9'+struct.pack('<i',CAVE-SITE-5)+b'\x90\x90'


def install(process, action):
    site=process.read(SITE,len(ORIGINAL))
    patched=PATCH+ORIGINAL[len(PATCH):]
    current=process.read(CAVE,len(CODE))
    if action=='etat':
        return 'enabled' if site==patched and current==CODE else 'disabled' if site==ORIGINAL else 'unrecognized'
    if action=='retirer':
        if site==ORIGINAL: return 'already disabled'
        if site!=patched or current!=CODE: raise ValueError('Unrecognized hook; nothing changed')
        process.write(SITE,ORIGINAL[:7])
        return 'disabled; cave retained inactive'
    if action!='poser': raise ValueError('Use poser, retirer or etat')
    if (process.read(GENERIC,len(NATIVE_PROLOGUE))!=NATIVE_PROLOGUE or site not in (ORIGINAL,patched)
            or process.qword(server_health.CALLBACK_FUNCTION)!=server_health.CODE_ADDRESS
            or process.read(server_health.CODE_ADDRESS,len(server_health.CODE))!=server_health.CODE):
        raise ValueError('Supported native routine/current impact callback not verified; nothing changed')
    if site==patched:
        if current!=CODE: raise ValueError('Live hook differs; restart the game')
        return 'already enabled'
    if current!=CODE and set(current)!={0xCC}: raise ValueError('Reserved code space occupied; nothing changed')
    process.write(CAVE,CODE)
    process.write(SITE,PATCH)
    return 'enabled for bridge impacts only; test shield absorption and resistant/immune hits separately'


if __name__=='__main__':
    if len(sys.argv)!=3: raise SystemExit(__doc__)
    if os.name!='nt': raise SystemExit('This experimental installer is for native Windows')
    print(install(Process(int(sys.argv[1])),sys.argv[2]))
