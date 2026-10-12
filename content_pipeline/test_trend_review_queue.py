"""Tests for the discovery-only trend review queue (no media generation/download)."""
# One-time validation trigger marker; tests only, no runtime side effects.
import unittest
from datetime import datetime, timedelta, timezone

from content_pipeline.trend_review_queue import build_queue, infer_category, select_trends


class TrendReviewQueueTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        self.feed = {
            "updated_at": self.now.isoformat(),
            "signals": [
                {
                    "id": "youtube:ai001",
                    "title": "Which AI is smartest?",
                    "url": "https://www.youtube.com/watch?v=ai001",
                    "category": "travel",
                    "chart_category_id": "28",
                    "demand_score": 94.5,
                    "views": 5000000,
                    "chart_region_count": 4,
                    "published_at": (self.now - timedelta(hours=5)).isoformat(),
                },
                {
                    "id": "youtube:animal001",
                    "title": "How Cookiecutter Sharks Bite Whales",
                    "url": "https://www.youtube.com/watch?v=animal001",
                    "category": "other",
                    "chart_category_id": "15",
                    "demand_score": 90.1,
                    "views": 2500000,
                    "chart_region_count": 3,
                    "published_at": (self.now - timedelta(hours=3)).isoformat(),
                },
            ],
        }

    def test_title_overrides_misleading_existing_category(self):
        self.assertEqual(infer_category("Which AI is smartest?", "travel", "28"), "tech")
        items, reason = select_trends(self.feed, state={}, now=self.now, limit=5)
        self.assertEqual(reason, "ok")
        self.assertEqual(items[0]["category"], "tech")
        self.assertTrue(all(item["media_downloaded"] is False for item in items))
        self.assertTrue(all(item["public_publishing_attempted"] is False for item in items))

    def test_selects_top_fresh_candidates_and_deduplicates_seen_signals(self):
        items, reason = select_trends(
            self.feed,
            state={"recent_signal_ids": ["youtube:ai001"], "recent_urls": []},
            now=self.now,
            limit=5,
        )
        self.assertEqual(reason, "ok")
        self.assertEqual([item["signal_id"] for item in items], ["youtube:animal001"])

    def test_stale_feed_fails_closed(self):
        feed = dict(self.feed)
        feed["updated_at"] = (self.now - timedelta(hours=19)).isoformat()
        items, reason = select_trends(feed, state={}, now=self.now)
        self.assertEqual(items, [])
        self.assertEqual(reason, "trend_radar_feed_stale")

    def test_old_low_demand_sensitive_and_future_signals_are_filtered(self):
        feed = dict(self.feed)
        feed["signals"] = [
            {
                "id": "low", "title": "Which AI is smartest?",
                "url": "https://www.youtube.com/watch?v=low", "category": "tech",
                "demand_score": 69.9, "published_at": (self.now - timedelta(hours=1)).isoformat(),
            },
            {
                "id": "political", "title": "The president discusses election politics",
                "url": "https://www.youtube.com/watch?v=political", "category": "tech",
                "demand_score": 99, "published_at": (self.now - timedelta(hours=1)).isoformat(),
            },
            {
                "id": "old", "title": "Which AI is smartest?",
                "url": "https://www.youtube.com/watch?v=old", "category": "tech",
                "demand_score": 99, "published_at": (self.now - timedelta(days=4)).isoformat(),
            },
            {
                "id": "future", "title": "Which AI is smartest?",
                "url": "https://www.youtube.com/watch?v=future", "category": "tech",
                "demand_score": 99, "published_at": (self.now + timedelta(hours=2)).isoformat(),
            },
        ]
        items, reason = select_trends(feed, state={}, now=self.now)
        self.assertEqual(items, [])
        self.assertEqual(reason, "no_fresh_supported_trend_candidates")

    def test_duplicates_and_missing_links_are_filtered(self):
        feed = dict(self.feed)
        feed["signals"] = [
            self.feed["signals"][0],
            dict(self.feed["signals"][0]),
            {
                "id": "missing-url", "title": "Which AI is smartest?", "url": "",
                "category": "tech", "demand_score": 99,
                "published_at": (self.now - timedelta(hours=1)).isoformat(),
            },
        ]
        items, reason = select_trends(feed, state={}, now=self.now)
        self.assertEqual(reason, "ok")
        self.assertEqual(len(items), 1)

    def test_build_queue_has_explicit_discovery_only_scope(self):
        queue = build_queue(self.feed, state={}, now=self.now, limit=2)
        self.assertEqual(queue["status"], "ready")
        self.assertEqual(queue["scope"], "trend_discovery_and_text_queue_only")
        self.assertFalse(queue["video_production"])
        self.assertFalse(queue["media_downloaded"])
        self.assertFalse(queue["public_publishing_attempted"])
        self.assertEqual(queue["count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
