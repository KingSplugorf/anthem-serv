"""Create a small, shareable PE layout report without executing the game.

Usage: py -3 inspect_game.py C:\\Games\\Anthem\\Anthem.exe
Produces Anthem-layout.json next to the executable (or use --output PATH).
No game assets or full executable contents are included in the report.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

SITES = {
    "solo_listen_flags": (0x14A60F7C1, "0000", "0100"),
    "solo_mode": (0x14A60F7CD, "03", "00"),
    "solo_start_state": (0x14A60F84B, "0f", "05"),
    "mission_connect_breakpoint": (0x14A60F852, None, None),
    "startup_state_breakpoint": (0x148010F00, None, None),
    "mission_gate_breakpoint": (0x148537DA0, None, None),
    "end_expedition": (0x1481F1482, "7456", "744e"),
    "end_expedition_callback": (0x1481F14D2, "b2014889f9ff5718", "b2014889f9ff5718"),
}


def inspect(executable):
    with open(executable, "rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        source.seek(0, 2)
        size = source.tell()
        def at(offset, length):
            if offset < 0 or offset + length > size:
                raise ValueError("Truncated or invalid PE file")
            source.seek(offset)
            value = source.read(length)
            if len(value) != length:
                raise ValueError("Truncated PE read")
            return value
        if at(0, 2) != b"MZ":
            raise ValueError("Not a PE executable")
        pe = struct.unpack("<I", at(0x3c, 4))[0]
        if at(pe, 4) != b"PE\0\0":
            raise ValueError("Invalid PE signature")
        machine, count = struct.unpack("<HH", at(pe + 4, 4))
        optional_size = struct.unpack("<H", at(pe + 20, 2))[0]
        optional = pe + 24
        if machine != 0x8664 or optional_size < 0x70 or struct.unpack("<H", at(optional, 2))[0] != 0x20b:
            raise ValueError("Expected a 64-bit x86 Windows executable")
        base = struct.unpack("<Q", at(optional + 24, 8))[0]
        image_size = struct.unpack("<I", at(optional + 56, 4))[0]
        flags = struct.unpack("<H", at(optional + 70, 2))[0]
        sections = []
        for index in range(count):
            section = at(optional + optional_size + 40 * index, 40)
            virtual_size, rva, raw_size, raw_offset = struct.unpack_from("<IIII", section, 8)
            sections.append(dict(name=section[:8].rstrip(b"\0").decode("ascii", errors="replace"),
                                 rva=rva, virtual_size=virtual_size, raw_size=raw_size, raw_offset=raw_offset))
        sites = {}
        for name, (address, original, patched) in SITES.items():
            rva = address - 0x140000000
            section = next((section for section in sections if section["rva"] <= rva < section["rva"] + section["virtual_size"]), None)
            entry = dict(address=hex(address), rva=hex(rva))
            length = len(bytes.fromhex(original)) if original else 16
            if section and rva-section["rva"]+length <= section["raw_size"]:
                value = at(section["raw_offset"]+rva-section["rva"], length).hex()
                entry.update(bytes=value, section=section["name"])
                if original:
                    entry["matches_known_bytes"] = value in (original, patched)
                    entry["original_bytes"] = original
            else:
                entry["note"] = "Address has no file-backed bytes; may be unpacked at runtime"
            sites[name] = entry
        return dict(file=Path(executable).name, size=size, sha256=digest, machine=hex(machine),
                    image_base=hex(base), image_size=image_size, dynamic_base=bool(flags & 0x40),
                    sections=sections, patch_sites=sites,
                    warning="Static layout only; this report does not validate decrypted runtime code or gameplay")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.executable.with_name(args.executable.stem + "-layout.json")
    output.write_text(json.dumps(inspect(args.executable), indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
