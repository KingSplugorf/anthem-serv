import io
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from combat_patches.ai import navigation


class NavigationStartupTests(unittest.TestCase):
    def test_installed_creator_does_not_fail_when_navpower_is_not_initialized(self):
        process = Mock()
        process.qword.return_value = 0
        output = io.StringIO()
        with patch.object(navigation, "Process", return_value=process), \
             patch.object(navigation, "place_creator") as install, \
             patch.object(navigation, "creator_state", return_value="créateur installé"), \
             patch.object(sys, "argv", ["navigation.py", "123", "poser"]), \
             patch.object(sys, "stdout", output):
            navigation.main()
        install.assert_called_once_with(process)
        process.qword.assert_called_once_with(navigation.SERVER_NAVPOWER)
        process.read.assert_not_called()
        self.assertIn("pas encore initialisé", output.getvalue())

    def test_initialized_system_still_reports_entities_and_meshes(self):
        process = Mock()
        process.qword.side_effect = [0x10000, 0, 0]
        process.read.return_value = struct.pack("<I", 3)
        self.assertEqual(navigation.navpower_state(process), "entités serveur 3, maillages 0 (types [])")
        process.read.assert_called_once_with(0x10028, 4)

    def test_actual_memory_read_failures_are_not_hidden(self):
        process = Mock()
        process.qword.side_effect = OSError("access denied")
        with self.assertRaisesRegex(OSError, "access denied"):
            navigation.navpower_state(process)


if __name__ == "__main__":
    unittest.main()
