from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from windows.inspect_game import tls_clues


class TlsInspectorTests(unittest.TestCase):
    def scan(self, data):
        with tempfile.TemporaryFile("w+b") as source:
            source.write(data)
            source.flush()
            result = tls_clues(source)
            source.seek(0)
            self.assertEqual(source.read(), data)
            return result

    def test_ascii_and_utf16_stack_clues_include_real_file_offsets(self):
        first = b"ProtoSSL: certificate validation failed"
        second = "InitializeSecurityContextW".encode("utf-16le")
        result = self.scan(b"\x00" + first + b"\x00\x00" + second + b"\x00\x00")
        self.assertEqual(result["candidate_stacks"], ["DirtySDK/ProtoSSL", "Windows Schannel"])
        self.assertEqual({entry["file_offset"] for entry in result["samples"]}, {"0x1", hex(len(first)+3)})
        self.assertEqual({entry["encoding"] for entry in result["samples"]}, {"ascii", "utf-16le"})

    def test_late_library_markers_are_found_after_sample_limit(self):
        data = b"\x00".join(f"certificate error {index}".encode() for index in range(100))
        result = self.scan(data + b"\x00ProtoSSL connection\x00")
        self.assertIn("DirtySDK/ProtoSSL", result["candidate_stacks"])
        self.assertTrue(result["samples_limited"])
        self.assertLessEqual(len(result["samples"]), 80)
        self.assertTrue(any("ProtoSSL" in entry["text"] for entry in result["samples"]))

    def test_unrelated_strings_do_not_imply_a_tls_stack(self):
        result = self.scan(b"MZ\x00ordinary game text\x00")
        self.assertEqual(result["candidate_stacks"], [])
        self.assertEqual(result["samples"], [])


if __name__ == "__main__":
    unittest.main()
