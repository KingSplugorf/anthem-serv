import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auto_patches


class CombatSetupRetryTests(unittest.TestCase):
    def test_transient_player_scan_failure_retries_without_marking_partial_setup_ready(self):
        process = Mock()
        output = io.StringIO()
        with patch.object(auto_patches, 'Process', return_value=process), \
             patch.object(auto_patches, 'alive', return_value=True), \
             patch.object(auto_patches, 'find_player', side_effect=[OSError('level object moved'), None]) as scan, \
             patch.object(auto_patches, 'follow') as follow, \
             patch.object(auto_patches.time, 'sleep'), contextlib.redirect_stdout(output):
            self.assertTrue(auto_patches.apply_mission_patches('123', ['ai/player_position.py']))
        self.assertEqual(scan.call_count, 2)
        follow.assert_called_once()
        process.memory.close.assert_called_once()
        self.assertIn('player scan not ready', output.getvalue())

    def test_cancelled_scan_does_not_start_followers_or_retry_stale_mission(self):
        process = Mock()
        cancelled = [False]
        def scan(_):
            cancelled[0] = True
            raise OSError('mission unloading')
        with patch.object(auto_patches, 'Process', return_value=process), \
             patch.object(auto_patches, 'alive', return_value=True), \
             patch.object(auto_patches, 'find_player', side_effect=scan), \
             patch.object(auto_patches, 'follow') as follow, contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(auto_patches.apply_mission_patches('123', ['ai/player_position.py'], lambda: cancelled[0]))
        follow.assert_not_called()
        process.memory.close.assert_called_once()
