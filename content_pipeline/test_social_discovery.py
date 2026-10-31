"""Tests for freshness-aware multi-platform candidate retention."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from content_pipeline import social_discovery
from content_pipeline.social_discovery import (
    QUERIES, UA, YOUTUBE_QUERIES, parse_youtube_duration, sort_discovery_items,
    youtube_query_for_time, youtube_queries_for_time, quota_ledger_for_run,
    discover_youtube_with_budget, FIRECRAWL_MONTHLY_CREDIT_BUDGET,
    YOUTUBE_DAILY_SEARCH_BUDGET, attach_view_trajectory,
    select_language_weighted_queries,
    normalize_native_telegram_candidates,
)


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

    def test_gizmiz_style_telegram_channels_get_priority_tiebreak(self):
        now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
        items = [
            {
                "url": "https://t.me/gizmiztel/100",
                "title": "ویدئوی بامزه سگ",
                "description": "لحظه جالب حیوانات",
                "raw": {
                    "source": "telegram_native_video",
                    "telegram_channel": "gizmiztel",
                    "telegram_post_views": 50000,
                    "telegram_published_at": (now - timedelta(hours=2)).isoformat(),
                    "age_hours": 2,
                },
            },
            {
                "url": "https://t.me/ashpaziroghi/100",
                "title": "ویدئوی بامزه سگ",
                "description": "لحظه جالب حیوانات",
                "raw": {
                    "source": "telegram_native_video",
                    "telegram_channel": "ashpaziroghi",
                    "telegram_post_views": 50000,
                    "telegram_published_at": (now - timedelta(hours=2)).isoformat(),
                    "age_hours": 2,
                },
            },
        ]
        normalized = normalize_native_telegram_candidates(items, now)
        by_channel = {row["telegram_channel"]: row for row in normalized}
        self.assertEqual(by_channel["gizmiztel"]["telegram_channel_priority"], 0)
        self.assertEqual(by_channel["ashpaziroghi"]["telegram_channel_priority"], 1)

    def test_view_trajectory_measures_velocity_and_acceleration_between_snapshots(self):
        now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
        old = {
            "id": "telegram-123",
            "url": "https://t.me/gizmiztel/123",
            "source": "telegram_native_video",
            "views": 50000,
            "discovered_at": "2026-10-14T11:00:00+00:00",
            "metrics_observed_at": "2026-10-14T11:00:00+00:00",
            "views_history": [
                {"observed_at": "2026-10-14T10:00:00+00:00", "views": 30000},
                {"observed_at": "2026-10-14T11:00:00+00:00", "views": 50000},
            ],
        }
        current = {
            "id": "telegram-123",
            "url": "https://t.me/gizmiztel/123",
            "source": "telegram_native_video",
            "views": 90000,
            "age_hours": 2,
        }
        result = attach_view_trajectory([current], [old], now)[0]
        self.assertEqual(result["views_velocity_kind"], "observed")
        self.assertEqual(result["views_delta_last_interval"], 40000)
        self.assertEqual(result["views_measurement_interval_hours"], 1.0)
        self.assertEqual(result["views_velocity_per_hour"], 40000)
        self.assertGreater(result["views_acceleration_per_hour2"], 0)
        self.assertEqual(len(result["views_history"]), 3)

    def test_first_seen_view_rate_is_marked_as_estimated_not_observed(self):
        now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
        current = {
            "id": "new-telegram-post",
            "url": "https://t.me/gizmiztel/321",
            "source": "telegram_native_video",
            "views": 40000,
            "age_hours": 4,
        }
        result = attach_view_trajectory([current], [], now)[0]
        self.assertEqual(result["views_velocity_kind"], "estimated_from_lifetime_views")
        self.assertEqual(result["views_velocity_per_hour"], 10000)
        self.assertEqual(len(result["views_history"]), 1)

    def test_youtube_iso_duration_is_parsed(self):
        self.assertEqual(parse_youtube_duration("PT17S"), 17)
        self.assertEqual(parse_youtube_duration("PT1M30S"), 90)
        self.assertEqual(parse_youtube_duration("PT2H3M4S"), 7384)
        self.assertIsNone(parse_youtube_duration("invalid"))


    def test_youtube_query_batch_prioritizes_persian_but_keeps_global_discovery(self):
        rows = youtube_queries_for_time(
            datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc), count=3
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(1 for _query, language in rows if language == "fa"), 2)
        self.assertEqual(sum(1 for _query, language in rows if language == "en"), 1)

    def test_single_youtube_search_prefers_persian_query(self):
        row = youtube_queries_for_time(
            datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc), count=1
        )[0]
        self.assertEqual(row[1], "fa")

    def test_firecrawl_and_bing_query_selection_favors_persian_and_rotates(self):
        first_time = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
        next_time = datetime(2026, 10, 10, 11, 0, tzinfo=timezone.utc)
        first = select_language_weighted_queries(
            QUERIES, first_time, count=2, window_seconds=3600
        )
        second = select_language_weighted_queries(
            QUERIES, next_time, count=2, window_seconds=3600
        )
        self.assertEqual(len(first), 2)
        self.assertEqual(sum(1 for query in first if re.search(r"[\u0600-\u06ff]", query)), 1)
        self.assertEqual(sum(1 for query in first if not re.search(r"[\u0600-\u06ff]", query)), 1)
        self.assertNotEqual(first, second)

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


    def test_firecrawl_query_bank_covers_major_video_platforms_and_broad_web(self):
        platforms = [social_discovery.expected_platform_for_query(query) for query in QUERIES]
        self.assertGreaterEqual(len(QUERIES), 12)
        for platform in ("x", "aparat", "tiktok", "instagram", "youtube", "facebook", "reddit", "vimeo", "dailymotion"):
            with self.subTest(platform=platform):
                self.assertIn(platform, platforms)
        self.assertTrue(any(not platform for platform in platforms), "include at least one cross-web query")
        # Keep test expectations aligned with the deliberate quota envelope:
        # one Firecrawl search per discovery pass and two YouTube searches per run.
        self.assertEqual(social_discovery.FIRECRAWL_SEARCHES_PER_RUN, 1)
        self.assertEqual(social_discovery.YOUTUBE_SEARCHES_PER_RUN, 2)

    def test_individual_post_url_validation_covers_new_platforms(self):
        valid = [
            ("https://www.facebook.com/reel/123456789", "facebook"),
            ("https://www.reddit.com/r/videos/comments/abc123/a_clip", "reddit"),
            ("https://vimeo.com/123456789", "vimeo"),
            ("https://www.dailymotion.com/video/x8abcde", "dailymotion"),
        ]
        for url, expected in valid:
            with self.subTest(url=url):
                self.assertEqual(social_discovery.platform(url), expected)
                self.assertTrue(social_discovery.is_video_post(url))

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
                self.assertEqual(
                    firecrawl.call_count,
                    social_discovery.FIRECRAWL_SEARCHES_PER_RUN,
                )
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


    def test_youtube_candidates_keep_audio_and_default_language_metadata(self):
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        now = datetime(2026, 10, 29, 21, 0, tzinfo=timezone.utc)
        search_payload = {
            "items": [{"id": {"videoId": "persianDemo123"}}]
        }
        details_payload = {
            "items": [{
                "id": "persianDemo123",
                "snippet": {
                    "title": "ویدیوی فارسی جالب",
                    "description": "یک ویدیوی کوتاه",
                    "publishedAt": "2026-10-29T20:00:00Z",
                    "defaultAudioLanguage": "fa",
                    "defaultLanguage": "fa",
                },
                "statistics": {"viewCount": "12000", "likeCount": "400", "commentCount": "35"},
                "contentDetails": {"duration": "PT18S"},
            }]
        }
        with (
            patch.object(social_discovery, "YOUTUBE_KEY", "demo-key"),
            patch.object(social_discovery, "YOUTUBE_API_ENABLED", True),
            patch(
                "content_pipeline.social_discovery.requests.get",
                side_effect=[FakeResponse(search_payload), FakeResponse(details_payload)],
            ) as get,
        ):
            rows = social_discovery.discover_youtube_public_candidates(
                now=now, max_search_calls=1
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["audio_language"], "fa")
        self.assertEqual(rows[0]["video_default_language"], "fa")
        self.assertEqual(get.call_count, 2)

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


    def test_quota_ledger_persists_usage_and_resets_youtube_at_utc_day_boundary(self):
        first = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)
        previous = {"quota_ledger": {
            "firecrawl_month": "2026-10",
            "firecrawl_credits_used": 602,
            "youtube_day": "2026-10-11",
            "youtube_search_calls_used": 17,
        }}
        same_day = quota_ledger_for_run(previous, first)
        self.assertEqual(same_day["firecrawl_credits_used"], 602)
        self.assertEqual(same_day["youtube_search_calls_used"], 17)
        self.assertEqual(same_day["firecrawl_monthly_credit_budget"], FIRECRAWL_MONTHLY_CREDIT_BUDGET)
        self.assertEqual(same_day["youtube_daily_search_budget"], YOUTUBE_DAILY_SEARCH_BUDGET)

        next_day = quota_ledger_for_run(previous, datetime(2026, 10, 12, 0, 1, tzinfo=timezone.utc))
        self.assertEqual(next_day["firecrawl_credits_used"], 602)
        self.assertEqual(next_day["youtube_search_calls_used"], 0)

    def test_first_quota_ledger_today_holds_youtube_calls_after_observed_exhaustion(self):
        first_run_today = quota_ledger_for_run(
            {},
            datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(first_run_today["youtube_search_calls_used"], YOUTUBE_DAILY_SEARCH_BUDGET)
        self.assertEqual(first_run_today["firecrawl_credits_used"], 400)

    def test_invalid_quota_counter_state_does_not_crash(self):
        ledger = quota_ledger_for_run(
            {"quota_ledger": {
                "firecrawl_month": "2026-10",
                "firecrawl_credits_used": "not-a-number",
                "youtube_day": "2026-10-11",
                "youtube_search_calls_used": None,
            }},
            datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(ledger["firecrawl_credits_used"], 0)
        self.assertEqual(ledger["youtube_search_calls_used"], 0)

    def test_quota_ledger_resets_monthly_firecrawl_count(self):
        previous = {"quota_ledger": {
            "firecrawl_month": "2026-10",
            "firecrawl_credits_used": 780,
            "youtube_day": "2026-10-31",
            "youtube_search_calls_used": 24,
        }}
        current = quota_ledger_for_run(previous, datetime(2026, 11, 1, 0, 5, tzinfo=timezone.utc))
        self.assertEqual(current["firecrawl_credits_used"], 0)
        self.assertEqual(current["youtube_search_calls_used"], 0)

    def test_youtube_budget_reserves_only_available_search_calls(self):
        now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        ledger = quota_ledger_for_run({"quota_ledger": {
            "firecrawl_month": "2026-10",
            "firecrawl_credits_used": 500,
            "youtube_day": "2026-10-12",
            "youtube_search_calls_used": 29,
        }}, now)
        with (
            patch.object(social_discovery, "YOUTUBE_KEY", "demo-key"),
            patch.object(social_discovery, "YOUTUBE_API_ENABLED", True),
            patch.object(social_discovery, "discover_youtube_public_candidates", return_value=[]) as discover,
        ):
            self.assertEqual(discover_youtube_with_budget(now, ledger), [])
        discover.assert_called_once_with(now, max_search_calls=1)
        self.assertEqual(ledger["youtube_search_calls_used"], YOUTUBE_DAILY_SEARCH_BUDGET)

    def test_push_style_run_skips_paid_search_and_keeps_public_telegram_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "social_discovery.json"
            with (
                patch.object(social_discovery, "PAID_SEARCH_ENABLED", False),
                patch.object(social_discovery, "KEY", ""),
                patch.object(social_discovery, "YOUTUBE_KEY", ""),
                patch.object(social_discovery, "YOUTUBE_API_ENABLED", False),
                patch.object(social_discovery, "OUT", output),
                patch.object(social_discovery, "firecrawl_search") as firecrawl,
                patch.object(social_discovery, "bing_search") as bing,
                patch.object(social_discovery, "rank_candidates", side_effect=lambda rows, limit=500: list(rows)[:limit]),
                patch.object(social_discovery, "log_growth_summary"),
                patch.object(social_discovery, "telegram_repost_candidates", return_value=[]) as telegram,
            ):
                self.assertEqual(social_discovery.main(), 0)
                firecrawl.assert_not_called()
                bing.assert_not_called()
                telegram.assert_called_once()
                payload = json.loads(output.read_text(encoding="utf-8"))
                self.assertIn("quota_ledger", payload)
                self.assertEqual(payload["items"], [])

if __name__ == "__main__":
    unittest.main(verbosity=2)
