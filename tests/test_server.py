from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from server import cleanup_expired_jobs


class ServerLifecycleTests(unittest.TestCase):
    def test_cleanup_removes_only_expired_job_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale = root / "20260714-120000-deadbeef"
            fresh = root / "20260714-130000-cafebabe"
            unrelated = root / "templates"
            stale.mkdir()
            fresh.mkdir()
            unrelated.mkdir()
            (stale / "document.docx").write_bytes(b"old")
            (fresh / "document.docx").write_bytes(b"new")
            (unrelated / "keep.txt").write_text("keep", encoding="utf-8")

            now = time.time()
            os.utime(stale, (now - 25 * 3600, now - 25 * 3600))
            os.utime(fresh, (now - 1 * 3600, now - 1 * 3600))

            removed = cleanup_expired_jobs(root, retention_hours=24, now=now)

            self.assertEqual(removed, 1)
            self.assertFalse(stale.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue((unrelated / "keep.txt").exists())

    def test_cleanup_is_disabled_at_zero_hours(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory) / "20260714-120000-deadbeef"
            job.mkdir()
            self.assertEqual(cleanup_expired_jobs(Path(directory), retention_hours=0), 0)
            self.assertTrue(job.exists())


if __name__ == "__main__":
    unittest.main()
