"""Exercise follower/patch entry paths that previously shadowed player imports."""
import contextlib
import io
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import Mock, patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / 'combat_patches/ai'))
import player_position
import shots


class CombatWorkerRegressionTests(unittest.TestCase):
    def test_position_follower_reads_the_player_slot_on_its_first_iteration(self):
        process = Mock()
        process.qword.return_value = 0
        with patch.object(player_position, 'Process', return_value=process), \
             patch.object(player_position, 'process_alive', side_effect=[True, False]), \
             patch.object(player_position.time, 'sleep'), \
             patch.object(player_position, 'refresh_player', return_value=False), \
             patch.object(sys, 'argv', ['player_position.py', '123']), \
             contextlib.redirect_stdout(io.StringIO()):
            player_position.main()
        process.qword.assert_any_call(player_position.player.PLAYER_SLOT)

    def test_shots_status_reads_the_imported_player_slot(self):
        process = Mock()
        process.read.side_effect = [shots.call(shots.ORIGINAL_TARGET), struct.pack('<IIfI', 0, 0, 0, 0)]
        process.qword.return_value = 0
        with patch.object(shots, 'Process', return_value=process), \
             patch.object(sys, 'argv', ['shots.py', '123', 'etat']), \
             contextlib.redirect_stdout(io.StringIO()):
            shots.main()
        process.qword.assert_called_once_with(shots.player.PLAYER_SLOT)
