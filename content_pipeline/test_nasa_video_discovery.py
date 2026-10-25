import unittest

from content_pipeline.nasa_video_discovery import allowed_nasa_metadata, merge_candidates
from content_pipeline.social_discovery import platform, is_video_post


class NasaQueueCompatibilityTests(unittest.TestCase):
    def test_nasa_detail_pages_are_recognized_as_video_posts(self):
        url = "https://images.nasa.gov/details/NASA001_ABC"
        self.assertEqual(platform(url), "nasa")
        self.assertTrue(is_video_post(url))
        self.assertFalse(is_video_post("https://images.nasa.gov/search?q=Earth"))


class NasaRightsScreenTests(unittest.TestCase):
    def test_nasa_centre_record_without_visible_rights_flags_passes_screen(self):
        accepted, reason = allowed_nasa_metadata({
            "center": "NASA",
            "secondary_creator": "",
            "title": "Earth at night from orbit",
            "description": "Views of Earth from orbit during a NASA observation.",
            "keywords": ["Earth", "orbit", "science"],
        })
        self.assertTrue(accepted)
        self.assertEqual(reason, "metadata_passed_conservative_nasa_screen")

    def test_non_nasa_centre_is_rejected(self):
        accepted, reason = allowed_nasa_metadata({
            "center": "ESA",
            "title": "Earth at night",
            "description": "A space video.",
        })
        self.assertFalse(accepted)
        self.assertEqual(reason, "non_nasa_center")

    def test_secondary_creator_is_rejected(self):
        accepted, reason = allowed_nasa_metadata({
            "center": "NASA",
            "secondary_creator": "European Space Agency",
            "title": "Earth at night",
            "description": "A space video.",
        })
        self.assertFalse(accepted)
        self.assertEqual(reason, "secondary_creator_present")

    def test_copyright_warning_is_rejected(self):
        accepted, reason = allowed_nasa_metadata({
            "center": "NASA",
            "title": "Earth at night",
            "description": "Copyrighted third-party soundtrack included.",
        })
        self.assertFalse(accepted)
        self.assertEqual(reason, "rights_warning_in_metadata")

    def test_identifiable_person_or_music_signal_is_rejected(self):
        for description in (
            "An astronaut interview aboard the station.",
            "Earth imagery with music soundtrack.",
        ):
            accepted, reason = allowed_nasa_metadata({
                "center": "NASA",
                "title": "Space station footage",
                "description": description,
            })
            self.assertFalse(accepted)
            self.assertEqual(reason, "person_or_audio_signal")

    def test_merge_preserves_other_sources_and_deduplicates_nasa_url(self):
        payload = {
            "version": 2,
            "items": [
                {"id": "legacy", "url": "https://t.me/example/10", "source": "telegram_native_video"},
                {"id": "nasa-1", "url": "https://images.nasa.gov/details/NASA001", "source": "nasa_public_domain_video"},
            ],
        }
        additions = [
            {"id": "nasa-1", "url": "https://images.nasa.gov/details/NASA001", "source": "nasa_public_domain_video"},
            {"id": "nasa-2", "url": "https://images.nasa.gov/details/NASA002", "source": "nasa_public_domain_video"},
        ]
        merged = merge_candidates(payload, additions, "2026-10-25T00:00:00+00:00")
        urls = [row["url"] for row in merged["items"]]
        self.assertIn("https://t.me/example/10", urls)
        self.assertEqual(urls.count("https://images.nasa.gov/details/NASA001"), 1)
        self.assertIn("https://images.nasa.gov/details/NASA002", urls)
        self.assertEqual(merged["nasa_video_discovery"]["new_candidates"], 1)


if __name__ == "__main__":
    unittest.main()
