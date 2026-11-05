"""An expedition-scoped write lease shared by the debugger and its workers.

Invalidation waits for in-flight guarded writes before allowing a return/new
mission to proceed. A worker from an earlier generation can never write under
the next generation's lease. Uses user-owned local files; no system privileges.
"""
from contextlib import contextmanager
import mmap
import os
from pathlib import Path
import struct
import threading
import time

HEADER = struct.Struct('<8s16sQQ')
MAGIC = b'ANTHMSN1'
_locks, _locks_guard = {}, threading.Lock()


class MissionInactive(OSError):
    pass


class Lease:
    def __init__(self, path, run_id, *, create=False):
        self.path = Path(path)
        self.token = bytes.fromhex(run_id)
        if len(self.token) != 16:
            raise ValueError('Expected a 128-bit mission run ID')
        if create:
            with self.path.open('xb') as output:
                output.write(HEADER.pack(MAGIC, self.token, 0, 0))
        self.file = self.path.open('r+b', buffering=0)
        try:
            if os.fstat(self.file.fileno()).st_size != HEADER.size:
                raise MissionInactive('Invalid mission lease size')
            self.mapping = mmap.mmap(self.file.fileno(), HEADER.size, access=mmap.ACCESS_WRITE)
            if self.mapping[:24] != MAGIC + self.token:
                self.mapping.close()
                raise MissionInactive('Mission lease belongs to another run')
        except BaseException:
            self.file.close()
            raise
        key = os.path.normcase(str(self.path.resolve()))
        with _locks_guard:
            self.thread_lock = _locks.setdefault(key, threading.RLock())

    @contextmanager
    def locked(self, timeout=5):
        with self.thread_lock:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        self.file.seek(0)
                        msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise MissionInactive('Timed out acquiring mission write barrier')
                    time.sleep(0.005)
            try:
                yield
            finally:
                if os.name == 'nt':
                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)

    def active(self, generation):
        magic, token, current, active = HEADER.unpack(self.mapping[:])
        return magic == MAGIC and token == self.token and current == generation and active == 1

    @contextmanager
    def guard(self, generation):
        with self.locked():
            if not self.active(generation):
                raise MissionInactive(f'Expedition {generation} is no longer active')
            yield

    def activate(self, generation):
        with self.locked():
            current = HEADER.unpack(self.mapping[:])[2]
            if generation <= 0 or generation < current:
                raise MissionInactive('Cannot activate an older expedition')
            self.mapping[:] = HEADER.pack(MAGIC, self.token, generation, 1)

    def deactivate(self, generation=None):
        with self.locked():
            current = HEADER.unpack(self.mapping[:])[2]
            if generation is not None and current != generation:
                return False
            self.mapping[:] = HEADER.pack(MAGIC, self.token, current, 0)
            return True

    def close(self):
        self.mapping.close()
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class GuardedMemory:
    def __init__(self, memory, lease, generation):
        self.memory, self.lease, self.generation = memory, lease, generation

    def __getattr__(self, name):
        return getattr(self.memory, name)

    def read_at(self, address, size):
        if hasattr(self.memory, 'read_at'):
            return self.memory.read_at(address, size)
        return os.pread(self.memory.fileno(), size, address)

    def write(self, data):
        with self.lease.guard(self.generation):
            return self.memory.write(data)

    def close(self):
        try:
            self.memory.close()
        finally:
            self.lease.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
