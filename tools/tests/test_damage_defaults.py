import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

path = Path(__file__).resolve().parents[1]/'combat_patches/health/damage.py'
sys.path.insert(0, str(path.parent))
spec = importlib.util.spec_from_file_location('combat_damage_defaults', path)
damage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(damage)


class DamageDefaultTests(unittest.TestCase):
    def test_default_install_is_neutral_and_retains_explicit_custom_bonus(self):
        for current, expected in ((0.0, 1.0), (2.5, 2.5)):
            process = Mock()
            bonus = [current]
            process.qword.return_value = damage.ORIGINAL
            process.read.side_effect = lambda address, size: damage.HEALTH_CODE if address == damage.HEALTH_CODE_ADDRESS else b'\xcc'*size
            process.float.side_effect = lambda address: bonus[0]
            process.write_float.side_effect = lambda address, value: bonus.__setitem__(0, value)
            with patch.object(damage, 'Process', return_value=process), \
                 patch.object(sys, 'argv', ['damage.py', '123', 'poser']), \
                 contextlib.redirect_stdout(io.StringIO()):
                damage.main()
            self.assertEqual(bonus[0], expected)
            if current: process.write_float.assert_not_called()
            else: process.write_float.assert_called_once_with(damage.PLAYER_BONUS, 1.0)
