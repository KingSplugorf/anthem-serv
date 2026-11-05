"""Owned child processes for one expedition's setup and followers."""
import subprocess
import threading


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

    def run(self, command, **options):
        child = self.start(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
        try:
            while True:
                try:
                    stdout, stderr = child.communicate(timeout=0.2)
                    return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    if self.cancelled():
                        self.stop_child(child)
        finally:
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
