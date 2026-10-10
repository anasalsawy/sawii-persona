"""Tests for harmonist log rotation."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sawii_persona.brains.harmonist import _rotate_if_needed, _write_log, HarmonistPass


class LogRotationTests(unittest.TestCase):
    def test_no_rotation_when_file_small(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "h.log")
            # Write a small file
            with open(path, "w") as f:
                f.write("small")
            _rotate_if_needed(path, max_bytes=1000, keep=3)
            self.assertTrue(os.path.exists(path))
            self.assertFalse(os.path.exists(path + ".1"))

    def test_rotation_when_file_too_big(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "h.log")
            with open(path, "w") as f:
                f.write("X" * 2000)
            _rotate_if_needed(path, max_bytes=1000, keep=3)
            # Original is gone, .1 exists
            self.assertTrue(os.path.exists(path + ".1"))
            # The .1 file has the original content
            with open(path + ".1") as f:
                self.assertEqual(f.read(), "X" * 2000)

    def test_rotation_keeps_only_n_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "h.log")
            for i in range(5):
                # Each time the file is "too big", it rotates.
                with open(path, "w") as f:
                    f.write(f"content-{i} " + "X" * 1500)
                _rotate_if_needed(path, max_bytes=1000, keep=3)
            # We should have .1, .2, .3 but not .4 or .5
            self.assertTrue(os.path.exists(path + ".1"))
            self.assertTrue(os.path.exists(path + ".2"))
            self.assertTrue(os.path.exists(path + ".3"))
            self.assertFalse(os.path.exists(path + ".4"))
            self.assertFalse(os.path.exists(path + ".5"))

    def test_write_log_includes_event_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "h.log")  # also tests makedirs
            record = HarmonistPass(started_at=100.0, finished_at=101.0,
                                    baseline_score=0.7, final_score=0.75,
                                    converged=False, status="improved")
            record.accepted.append({"target": "settings.x", "delta": 0.05,
                                     "rationale": "test"})
            _write_log(path, record)
            with open(path) as f:
                line = f.readline().strip()
            data = json.loads(line)
            self.assertEqual(data["event"], "harmonist_pass")
            self.assertIn("wall_clock_ms", data)
            self.assertIn("status", data)
            self.assertIn("delta", data)
            self.assertEqual(data["status"], "improved")
            self.assertEqual(data["delta"], 0.05)

    def test_write_log_creates_parent_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "deep", "nested", "h.log")
            record = HarmonistPass(started_at=1.0, finished_at=2.0)
            _write_log(path, record)
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()