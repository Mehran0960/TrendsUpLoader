"""Tests for freshness-aware multi-platform candidate retention."""
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from content_pipeline import social_discovery
from content_pipeline.social_discovery import QUERIES, UA, YOUTUBE_QUERIES, parse_youtube_duration, sort_discovery_items, youtube_query_for_time, youtube_queries_for_time


class SocialDiscoveryTests(unittest.TestCase):
    def test_fresh_candidates_are_not_dropped_behind_old_history(self):
        rows = [
            {
                "url": "https://www.instagram.com/reel/old/",
                "discovered_at": "2026-10-01T10:00:00+00:00",
                "views": 900000,
            },
            {
                "url": "https://www.instagram.com/reel/new/",
                "discovered_at": "2026-10-10T10:00:00+00:00",
                "views": 1200,
            },
        ]
        kept = sort_discovery_items(rows, limit=1)
        self.assertEqual([x["url"] for x in kept], ["https://www.instagram.com/reel/new/"])

    def test_engagement_breaks_ties_only_after_freshness(self):
        rows = [
            {"url": "https://example.com/low/", "discovered_at": "2026-10-10T10:00:00+00:00", "views": 100},
            {"url": "https://example.com/high/", "discovered_at": "2026-10-10T10:00:00+00:00", "views": 200},
        ]
        kept = sort_discovery_items(rows)
        self.assertEqual(kept[0]["url"], "https://example.com/high/")

    def test_unknown_timestamps_sort_after_known_recent_candidates(self):
        rows = [
            {"url": "https://example.com/unknown/", "views": 999999},
            {"url": "https://example.com/known/", "discovered_at": "2026-10-10T10:00:00+00:00"},
        ]
        kept = sort_discovery_items(rows)
        self.assertEqual(kept[0]["url"], "https://example.com/known/")

    def test_youtube_iso_duration_is_parsed(self):
        self.assertEqual(parse_youtube_duration("PT17S"), 17)
        self.assertEqual(parse_youtube_duration("PT1M30S"), 90)
        self.assertEqual(parse_youtube_duration("PT2H3M4S"), 7384)
        self.assertIsNone(parse_youtube_duration("invalid"))

    def test_youtube_queries_rotate_between_half_hour_windows(self):
        first = youtube_query_for_time(datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc))
        second = youtube_query_for_time(datetime(2026, 10, 10, 10, 31, tzinfo=timezone.utc))
        self.assertNotEqual(first, second)

    def test_youtube_batch_returns_distinct_queries_and_rotates(self):
        first = youtube_queries_for_time(datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc), count=2)
        second = youtube_queries_for_time(datetime(2026, 10, 10, 10, 31, tzinfo=timezone.utc), count=2)
        self.assertEqual(len(first), 2)
        self.assertEqual(len(set(first)), 2)
        self.assertNotEqual(first, second)

    def test_limit_is_respected(self):
        rows = [
            {"url": f"https://example.com/{i}", "discovered_at": f"2026-10-10T10:{i:02d}:00+00:00"}
            for i in range(10)
        ]
        self.assertEqual(len(sort_discovery_items(rows, limit=3)), 3)


    def test_firecrawl_query_bank_uses_downloadable_individual_post_platforms(self):
        platforms = [social_discovery.expected_platform_for_query(query) for query in QUERIES]
        self.assertEqual(set(platforms), {"x", "aparat"})
        self.assertNotIn("tiktok", platforms)
        self.assertNotIn("instagram", platforms)
        self.assertNotIn("youtube", platforms)
        self.assertTrue(all("inurl:status" in query or "aparat.com/v" in query for query in QUERIES))

    def test_query_bank_covers_mainstream_evergreen_interests(self):
        searchable = " ".join([query for query, _language in YOUTUBE_QUERIES] + QUERIES).lower()
        for keyword in ("آشپزی", "ترفند", "بسکتبال", "حیات وحش", "آزمایش علمی", "کاردستی", "نوازندگی", "heartwarming"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword.lower(), searchable)


    def test_main_uses_firecrawl_keyless_before_bing_when_key_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "social_discovery.json"
            with (
                patch.object(social_discovery, "KEY", ""),
                patch.object(social_discovery, "OUT", output),
                patch.object(social_discovery, "firecrawl_search", return_value={"data": []}) as firecrawl,
                patch.object(social_discovery, "discover_youtube_public_candidates", return_value=[]),
                patch.object(social_discovery, "rank_candidates", return_value=[]),
                patch.object(social_discovery, "log_growth_summary"),
                patch.object(social_discovery, "bing_search", return_value=[]) as bing,
                patch.object(social_discovery, "telegram_repost_candidates", return_value=[]),
            ):
                self.assertEqual(social_discovery.main(), 0)
                self.assertEqual(firecrawl.call_count, 2)
                bing.assert_not_called()
                self.assertTrue(output.is_file())
                payload = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(payload["items"], [])


    def test_telegram_caption_gate_accepts_english_topic_captions_but_not_noise(self):
        self.assertTrue(
            social_discovery.telegram_caption_has_discovery_signal(
                "Funny cat has a surprising reaction to a toy"
            )
        )
        self.assertTrue(
            social_discovery.telegram_caption_has_discovery_signal(
                "Easy pasta recipe in under one minute"
            )
        )
        self.assertFalse(
            social_discovery.telegram_caption_has_discovery_signal(
                "Breaking report about a city council meeting"
            )
        )

    def test_main_includes_native_telegram_videos_when_firecrawl_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "social_discovery.json"
            telegram_candidate = {
                "url": "https://t.me/gizmiztel/987654",
                "title": "کلیپ بامزه سگ که یک حرکت عجیب انجام می‌دهد",
                "description": "ویدئوی کوتاه حیوانات",
                "raw": {
                    "source": "telegram_native_video",
                    "telegram_channel": "gizmiztel",
                    "telegram_post": "gizmiztel/987654",
                    "telegram_post_views": 25000,
                    "telegram_published_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                    "age_hours": 1.0,
                },
            }
            with (
                patch.object(social_discovery, "KEY", ""),
                patch.object(social_discovery, "OUT", output),
                patch.object(social_discovery, "firecrawl_search", return_value={"data": []}),
                patch.object(social_discovery, "discover_youtube_public_candidates", return_value=[]),
                patch.object(social_discovery, "rank_candidates", side_effect=lambda rows, limit=500: list(rows)[:limit]),
                patch.object(social_discovery, "log_growth_summary"),
                patch.object(social_discovery, "telegram_repost_candidates", return_value=[telegram_candidate]) as telegram_scan,
            ):
                self.assertEqual(social_discovery.main(), 0)
                telegram_scan.assert_called_once()
                payload = json.loads(output.read_text(encoding="utf-8"))
                found = [x for x in payload["items"] if x["url"] == "https://t.me/gizmiztel/987654"]
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["source"], "telegram_native_video")
                self.assertEqual(found[0]["views"], 25000)
                self.assertAlmostEqual(found[0]["age_hours"], 1.0, places=2)

    def test_firecrawl_keyless_request_omits_authorization_header(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"data": []}

        with (
            patch.object(social_discovery, "KEY", ""),
            patch("content_pipeline.social_discovery.requests.post", return_value=FakeResponse()) as post,
        ):
            self.assertEqual(social_discovery.firecrawl_search("short video", limit=10), {"data": []})
            post.assert_called_once()
            kwargs = post.call_args.kwargs
            self.assertNotIn("Authorization", kwargs["headers"])
            self.assertEqual(kwargs["json"]["query"], "short video")
            self.assertEqual(kwargs["json"]["tbs"], "qdr:d")

    def test_search_result_freshness_uses_published_date_when_available(self):
        now = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)
        age, published, basis = social_discovery.search_result_freshness(
            {"raw": {"publishedAt": "2026-10-11T15:00:00Z"}}, now
        )
        self.assertEqual(age, 3.0)
        self.assertEqual(basis, "published_date")
        self.assertTrue(published.startswith("2026-10-11T15:00:00"))

    def test_search_result_freshness_uses_conservative_time_filter_bound_without_date(self):
        now = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(
            social_discovery.search_result_freshness({"raw": {}}, now),
            (24.0, "", "firecrawl_tbs_qdr_d_upper_bound"),
        )

    def test_youtube_api_calls_are_skipped_when_workflow_flag_is_disabled(self):
        with (
            patch.object(social_discovery, "YOUTUBE_KEY", "demo-key"),
            patch.object(social_discovery, "YOUTUBE_API_ENABLED", False),
            patch("content_pipeline.social_discovery.requests.get") as get,
        ):
            self.assertEqual(social_discovery.discover_youtube_public_candidates(), [])
            get.assert_not_called()

    def test_youtube_api_error_context_reports_status_without_exposing_key(self):
        class FakeResponse:
            status_code = 403

            def json(self):
                return {
                    "error": {
                        "code": 403,
                        "message": "YouTube Data API v3 is not enabled for key secret-demo-key",
                        "errors": [{"reason": "accessNotConfigured"}],
                    }
                }

        class FakeError(Exception):
            response = FakeResponse()
            reason = "Forbidden"

        with patch.object(social_discovery, "YOUTUBE_KEY", "secret-demo-key"):
            context = social_discovery.youtube_api_error_context(FakeError())
        self.assertEqual(context["http_status"], 403)
        self.assertEqual(context["api_reason"], "accessNotConfigured")
        self.assertNotIn("secret-demo-key", context["api_message"])
        self.assertIn("[redacted]", context["api_message"])

    def test_fallback_search_user_agent_is_configured(self):
        self.assertTrue(UA.startswith("Mozilla/"))
        self.assertIn("Chrome/", UA)


if __name__ == "__main__":
    unittest.main(verbosity=2)
