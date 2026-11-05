"""Owned child processes for one expedition's setup and followers."""
import subprocess
import threading
import tempfile
import time
import locale


class Children:
    def __init__(self, cancelled=lambda: False):
        self.cancelled, self.lock = cancelled, threading.Lock()
        self.children = set()

    def start(self, command, **options):
        if self.cancelled():
            raise InterruptedError('Expedition was cancelled before child startup')
        child = subprocess.Popen(command, **options)
        with self.lock:
            self.children.add(child)
        return child

    def run(self, command, timeout=120, **options):
        # File capture avoids waiting for pipe EOF after the direct process
        # exits, and needs no Windows communicate() reader threads on each poll.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            child = self.start(command, stdout=output, stderr=errors, **options)
            print(f'Patch child started: PID {child.pid}; {command}', flush=True)
            deadline, timed_out = time.monotonic() + timeout, False
            try:
                while child.poll() is None:
                    if self.cancelled():
                        self.stop_child(child)
                        break
                    if time.monotonic() >= deadline:
                        timed_out = True
                        self.stop_child(child)
                        break
                    try:
                        child.wait(timeout=min(0.2, max(0.001, deadline - time.monotonic())))
                    except subprocess.TimeoutExpired:
                        pass
                output.seek(0)
                errors.seek(0)
                stdout, stderr = output.read(4 << 20), errors.read(4 << 20)
                if timed_out:
                    stderr += f'\nPatch child timed out after {timeout:g} seconds; retry required\n'.encode()
                if options.get('text') or options.get('universal_newlines') or options.get('encoding'):
                    encoding = options.get('encoding') or locale.getpreferredencoding(False)
                    stdout = stdout.decode(encoding, errors=options.get('errors') or 'replace')
                    stderr = stderr.decode(encoding, errors=options.get('errors') or 'replace')
                return subprocess.CompletedProcess(command, 124 if timed_out else child.returncode, stdout, stderr)
            finally:
                self.stop_child(child)
                with self.lock:
                    self.children.discard(child)

    @staticmethod
    def stop_child(child):
        if child.poll() is None:
            try:
                child.terminate()
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)

    def close(self):
        with self.lock:
            children = list(self.children)
        for child in children:
            self.stop_child(child)
        with self.lock:
            self.children.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
