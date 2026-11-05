import ctypes as C
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import windows_memory as win


class ModuleSnapshotTests(unittest.TestCase):
    def kernel(self, snapshots):
        api = Mock()
        api.error = 24
        api.CreateToolhelp32Snapshot.side_effect = snapshots
        def first(handle, entry):
            self.assertEqual(entry._obj.dwSize, C.sizeof(win.MODULEENTRY32W))
            entry._obj.modBaseAddr = 0x140000000
            entry._obj.szExePath = r'C:\Games\Anthem\Anthem.exe'
            return 1
        def next_module(*_):
            api.error = 18
            return 0
        api.Module32FirstW.side_effect = first
        api.Module32NextW.side_effect = next_module
        api.CloseHandle.return_value = 1
        return api

    def test_transient_bad_length_retries_then_closes_only_successful_snapshot(self):
        api = self.kernel([C.c_void_p(-1).value, C.c_void_p(-1).value, 0x123456789])
        with patch.object(win, 'kernel', return_value=api), \
             patch.object(win, 'last_error', side_effect=lambda: api.error), \
             patch.object(win.time, 'sleep') as sleep:
            self.assertEqual(list(win.modules(123)), [(0x140000000, r'C:\Games\Anthem\Anthem.exe')])
        self.assertEqual(api.CreateToolhelp32Snapshot.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        api.CloseHandle.assert_called_once_with(0x123456789)

    def test_access_error_is_not_retried_or_disguised(self):
        api = self.kernel([C.c_void_p(-1).value])
        api.error = 5
        with patch.object(win, 'kernel', return_value=api), \
             patch.object(win, 'last_error', side_effect=lambda: api.error), \
             patch.object(win.time, 'sleep') as sleep:
            with self.assertRaises(OSError): list(win.modules(123))
        sleep.assert_not_called()
        api.CloseHandle.assert_not_called()

    def test_bad_length_retries_are_bounded_and_never_close_invalid_handles(self):
        api = self.kernel([C.c_void_p(-1).value] * 100)
        with patch.object(win, 'kernel', return_value=api), \
             patch.object(win, 'last_error', side_effect=lambda: api.error), \
             patch.object(win.time, 'sleep') as sleep:
            with self.assertRaises(OSError): list(win.modules(123))
        self.assertEqual(api.CreateToolhelp32Snapshot.call_count, 100)
        self.assertEqual(sleep.call_count, 99)
        api.CloseHandle.assert_not_called()

    def test_snapshot_is_closed_when_caller_only_reads_the_main_module(self):
        api = self.kernel([0x123456789])
        with patch.object(win, 'kernel', return_value=api):
            iterator = win.modules(123)
            next(iterator)
            iterator.close()
        api.CloseHandle.assert_called_once_with(0x123456789)
