"""Process memory shared by Linux and native 64-bit Windows patch tools.

Windows code is imported lazily so Linux tools keep working without Win32 DLLs.
All addresses remain specific to the game build documented by this repository.
"""
import os


def open_memory(pid, mode="r+b"):
    if os.name == "nt":
        from windows_memory import Memory
        return Memory(int(pid), writable="+" in mode)
    return open(f"/proc/{int(pid)}/mem", mode, buffering=0)


def read_at(memory, address, size):
    if hasattr(memory, "read_at"):
        return memory.read_at(address, size)
    return os.pread(memory.fileno(), size, address)


def writable_regions(pid):
    if os.name == "nt":
        from windows_memory import regions
        yield from regions(int(pid))
    else:
        with open(f"/proc/{int(pid)}/maps") as maps:
            for line in maps:
                addresses, permissions = line.split()[:2]
                if permissions.startswith("rw"):
                    yield tuple(int(part, 16) for part in addresses.split("-"))


def module_base(pid, name):
    if os.name == "nt":
        from windows_memory import modules
        for base, path in modules(int(pid)):
            if path.replace("\\", "/").rsplit("/", 1)[-1].lower() == name.lower():
                return base
    else:
        with open(f"/proc/{int(pid)}/maps") as maps:
            for line in maps:
                fields = line.split()
                if len(fields) >= 6 and fields[-1].lower().endswith(name.lower()):
                    return int(fields[0].split("-")[0], 16)
    raise OSError(f"{name} absent du processus {pid}")


def alive(pid):
    if os.name == "nt":
        from windows_memory import process_alive
        return process_alive(int(pid))
    return os.path.exists(f"/proc/{int(pid)}")
