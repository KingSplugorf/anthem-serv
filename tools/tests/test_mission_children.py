import contextlib
import io
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mission_workers import Children


class MissionChildrenTests(unittest.TestCase):
    def test_collects_large_stdout_and_stderr_without_pipe_waiting(self):
        with Children() as children, contextlib.redirect_stdout(io.StringIO()):
            result = children.run([sys.executable, '-c', "import sys; print('x'*200000); print('error detail',file=sys.stderr)"], text=True, encoding='utf-8')
            self.assertFalse(children.children)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout.strip()), 200000)
        self.assertIn('error detail', result.stderr)

    def test_hung_patch_is_stopped_and_returns_failure_for_retry(self):
        with Children() as children, contextlib.redirect_stdout(io.StringIO()):
            result = children.run([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=.1, text=True)
            self.assertFalse(children.children)
        self.assertEqual(result.returncode, 124)
        self.assertIn('timed out', result.stderr)

    def test_cancelled_patch_stops_and_preserves_byte_output(self):
        stop = threading.Event()
        timer = threading.Timer(.1, stop.set)
        timer.start()
        try:
            with Children(stop.is_set) as children, contextlib.redirect_stdout(io.StringIO()):
                result = children.run([sys.executable, '-c', 'import time; time.sleep(30)'])
                self.assertFalse(children.children)
            self.assertNotEqual(result.returncode, 0)
            self.assertIsInstance(result.stdout, bytes)
        finally:
            timer.cancel()
