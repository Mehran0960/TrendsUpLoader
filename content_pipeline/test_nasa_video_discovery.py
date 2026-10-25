import unittest

from unittest.mock import patch

from content_pipeline.nasa_video_discovery import (
    allowed_nasa_metadata,
    build_candidate,
    merge_candidates,
    mp4_media_url,
    search_slot,
)
from content_pipeline.publish_ifilo import rights_cleared_for_ifilo
from content_pipeline.social_discovery import platform, is_video_post


class NasaQueueCompatibilityTests(unittest.TestCase):
    def test_search_slot_rotates_every_half_hour(self):
        from datetime import datetime, timezone
        first = datetime(2026, 10, 25, 0, 7, tzinfo=timezone.utc)
        second = datetime(2026, 10, 25, 0, 37, tzinfo=timezone.utc)
        third = datetime(2026, 10, 25, 1, 7, tzinfo=timezone.utc)
        self.assertEqual(search_slot(second), search_slot(first) + 1)
        self.assertEqual(search_slot(third), search_slot(second) + 1)

    def test_mp4_manifest_prefers_verified_medium_derivative(self):
        manifest = {
            "collection": {
                "items": [
                    {"href": "https://images-assets.nasa.gov/video/ABC_123/ABC_123~orig.mp4"},
                    {"href": "https://images-assets.nasa.gov/video/ABC_123/ABC_123~medium.mp4"},
                    {"href": "https://example.com/video/ABC_123.mp4"},
                ]
            }
        }
        with patch("content_pipeline.nasa_video_discovery._request_json", return_value=manifest), patch(
            "content_pipeline.nasa_video_discovery._verify_official_mp4_url", return_value=True
        ):
            url = mp4_media_url("ABC_123")
        self.assertEqual(url, "https://images-assets.nasa.gov/video/ABC_123/ABC_123~medium.mp4")

    def test_official_mp4_fallback_is_verified_before_use(self):
        with patch(
            "content_pipeline.nasa_video_discovery._request_json",
            return_value={"collection": {"items": []}},
        ), patch(
            "content_pipeline.nasa_video_discovery._verify_official_mp4_url",
            side_effect=[False, True],
        ):
            url = mp4_media_url("ABC_123")
        self.assertEqual(url, "https://images-assets.nasa.gov/video/ABC_123/ABC_123~small.mp4")


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

    def test_recognized_nasa_centre_with_nasa_secondary_creator_passes(self):
        accepted, reason = allowed_nasa_metadata({
            "center": "JPL",
            "secondary_creator": "NASA",
            "title": "A planetary landscape",
            "description": "NASA robotic mission imagery of a planetary landscape.",
            "keywords": ["planetary science", "space"],
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

    def test_candidate_builder_emits_auditable_nasa_rights_record(self):
        api_item = {
            "data": [{
                "nasa_id": "NASA001_ABC",
                "media_type": "video",
                "center": "NASA",
                "secondary_creator": "",
                "title": "Earth at night from orbit",
                "description": "Views of Earth at night from orbital altitude.",
                "keywords": ["Earth", "orbit", "science"],
                "date_created": "2024-01-01T00:00:00Z",
            }]
        }
        media_url = "https://images-assets.nasa.gov/video/NASA001_ABC~medium.mp4"
        with patch("content_pipeline.nasa_video_discovery.mp4_media_url", return_value=media_url):
            candidate = build_candidate(
                api_item,
                "Earth from space",
                ["science_curiosity", "nature_and_wildlife"],
                "2026-10-25T00:00:00+00:00",
            )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["source"], "nasa_public_domain_video")
        self.assertEqual(candidate["direct_media_url"], media_url)
        self.assertTrue(rights_cleared_for_ifilo(candidate))

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
