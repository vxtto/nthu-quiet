import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location("nthu_quiet", Path(__file__).parents[1] / "nthu_quiet.py")
quiet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quiet)


class NativeHelpers(unittest.TestCase):
    def test_scan_rejects_unaligned_and_incidental_pointers(self):
        needle = struct.pack("<Q", 0x613C28747000)
        object_header = needle + struct.pack("<IIQ", 3, 0, 0)
        incidental = needle + struct.pack("<IIQ", 0x04002038, 0, 0)
        memory = object_header + incidental + b"x" + object_header
        self.assertEqual(quiet.scan_bytes(memory, 0x1000, needle), [0x1000])

    def test_zero_reference_is_rejected(self):
        needle = struct.pack("<Q", 0x12345678)
        self.assertEqual(quiet.scan_bytes(needle + b"\0" * 16, 0, needle), [])

    def test_chunk_overlap_can_recover_boundary_object(self):
        needle = struct.pack("<Q", 0x12345678)
        header = needle + struct.pack("<II", 1, 0)
        memory = b"\0" * 24 + header + b"\0" * 24
        found = set(quiet.scan_bytes(memory[:32], 0x1000, needle))
        found.update(quiet.scan_bytes(memory[16:], 0x1010, needle))
        self.assertEqual(found, {0x1018})

    def test_marker_missing_fails_closed(self):
        with self.assertRaises(quiet.QuietError):
            quiet.marker("ptrace: Operation not permitted", "CLASS")

    def test_marker_does_not_match_warning_text(self):
        self.assertEqual(quiet.marker("warning: CLASS=bad\nCLASS=0x123\n", "CLASS"), "0x123")


if __name__ == "__main__":
    unittest.main()
