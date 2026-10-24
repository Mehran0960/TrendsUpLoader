"""Regression tests for the optional Filo browser uploader."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from content_pipeline.filo_uploader import prepare_metadata, upload_video


class FiloUploaderTests(unittest.TestCase):
    def test_metadata_removes_social_noise_from_title_and_keeps_source(self):
        result = prepare_metadata({
            "title": "🔥😱 ۷۵٪ تخفیف!!  تونل شگفت‌انگیز!!! https://example.org/x",
            "description": "ماجرای کامل ویدیو",
            "url": "https://t.me/demo/123",
        })
        self.assertIn("تونل شگفت‌انگیز", result["title"])
        self.assertNotIn("https://", result["title"])
        self.assertIn("منبع ویدیو: https://t.me/demo/123", result["description"])

    def test_metadata_requires_a_real_title(self):
        with self.assertRaisesRegex(ValueError, "title_missing_or_too_short"):
            prepare_metadata({"title": "https://t.me/channel/123", "url": "https://t.me/channel/123"})

    def test_disabled_uploader_never_launches_browser(self):
        with patch.dict(os.environ, {"FILO_UPLOAD_ENABLED": "false"}, clear=False):
            with patch("content_pipeline.filo_uploader._start", side_effect=AssertionError("browser must not start")):
                result = upload_video("/does/not/exist.mp4", {"title": "A test title"})
        self.assertEqual(result["status"], "disabled")
        self.assertFalse(result["success"])

    def test_duplicate_success_is_skipped_before_browser_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.mp4"
            path.write_bytes(b"x" * 110_000)
            import hashlib
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            old = {"status": "submitted", "source_url": "https://t.me/demo/123", "sha256": digest}
            with patch.dict(os.environ, {"FILO_UPLOAD_ENABLED": "true"}, clear=False):
                with patch("content_pipeline.filo_uploader._start", side_effect=AssertionError("duplicate must not launch browser")):
                    result = upload_video(path, {"title": "A sample video", "url": "https://t.me/demo/123"}, [old])
        self.assertEqual(result["status"], "duplicate_skipped")
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
