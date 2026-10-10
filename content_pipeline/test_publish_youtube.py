"""Tests for rights-gated, private-only YouTube uploads."""
import json
import tempfile
import unittest
from pathlib import Path

from content_pipeline.publish_youtube import build_description, find_latest_video, validate_upload


class YouTubePublishTests(unittest.TestCase):
    def valid_meta(self):
        return {
            "quality_gate": "passed_publish",
            "script_quality": "passed",
            "script_mode": "curated",
            "duration_seconds": 25,
            "display_title_fa": "یک عکس؛ چطور هوش مصنوعی ازش ویدئو می‌سازه؟",
            "title": "AI Demo",
            "content_key": "visual_test:AI-demo",
            "source_url": "https://deepmind.google/technologies/veo/",
            "segments": [{"text": "این یک روایت اصیل و بررسی‌شده است."}],
        }

    def test_requires_all_rights_and_editorial_gates(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "video.mp4"
            video.write_bytes(b"video")
            meta = self.valid_meta()
            rights = {"assets_cleared": True, "automated_publishable": True, "media_status": "original_generated_visuals"}
            self.assertEqual(validate_upload(video, meta, rights), [])
            meta["script_mode"] = "ai"
            self.assertIn("script_not_curated_original", validate_upload(video, meta, rights))
            meta["script_mode"] = "curated"
            rights["assets_cleared"] = False
            self.assertIn("rights_manifest_not_cleared", validate_upload(video, meta, rights))

    def test_requires_video_metadata_and_rights_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                find_latest_video(folder)
            video = Path(folder) / "video.mp4"
            video.write_bytes(b"video")
            with self.assertRaises(RuntimeError):
                find_latest_video(folder)

    def test_description_includes_story_and_attribution_when_present(self):
        with tempfile.TemporaryDirectory() as folder:
            rights = Path(folder) / "rights_manifest.json"
            rights.write_text("{}", encoding="utf-8")
            (Path(folder) / "attribution.txt").write_text("Visual asset attribution record", encoding="utf-8")
            description = build_description(self.valid_meta(), rights)
            self.assertIn("یک عکس", description)
            self.assertIn("روایت اصیل", description)
            self.assertIn("https://deepmind.google/technologies/veo/", description)
            self.assertIn("Visual asset attribution record", description)

    def test_uploaded_title_can_be_based_on_original_display_title(self):
        meta = self.valid_meta()
        self.assertLessEqual(len(meta["display_title_fa"]), 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
