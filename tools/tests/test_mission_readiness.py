from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auto_patches


class MissionReadinessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.log = Path(temp.name) / 'active_mission_123.txt'
        self.debugger = Mock()
        self.debugger.poll.return_value = None

    def test_old_ready_marker_is_ignored_until_this_attach_reaches_initial_breakpoint(self):
        self.log.write_text(auto_patches.MISSION_DEBUGGER_READY + '\n', encoding='utf-8')
        start = self.log.stat().st_size
        def new_ready(_seconds):
            with self.log.open('a', encoding='utf-8') as output:
                output.write(auto_patches.MISSION_DEBUGGER_READY + '\n')
        with patch.object(auto_patches, 'alive', return_value=True), \
             patch.object(auto_patches, 'mission_log', return_value=str(self.log)), \
             patch.object(auto_patches.time, 'sleep', side_effect=new_ready) as sleep:
            self.assertTrue(auto_patches.wait_for_mission_debugger('123', start, self.debugger))
        sleep.assert_called_once()

    def test_exited_debugger_is_reported_before_any_ready_message(self):
        self.debugger.poll.return_value = 1
        self.debugger.returncode = 1
        with patch.object(auto_patches, 'alive', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'before readiness'):
                auto_patches.wait_for_mission_debugger('123', 0, self.debugger)

    def test_spawned_process_alone_does_not_count_as_ready(self):
        self.log.write_text('attaché (Win32) : attente\n', encoding='utf-8')
        with patch.object(auto_patches, 'alive', return_value=True), \
             patch.object(auto_patches, 'mission_log', return_value=str(self.log)), \
             patch.object(auto_patches.time, 'monotonic', side_effect=[0, 31]):
            with self.assertRaisesRegex(RuntimeError, 'did not become ready'):
                auto_patches.wait_for_mission_debugger('123', 0, self.debugger)


if __name__ == '__main__':
    unittest.main()
