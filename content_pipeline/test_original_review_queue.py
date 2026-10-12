"""Deterministic tests for trend-inspired original-video private review selection."""
# One-time smoke-trigger marker; no runtime behavior change.
import unittest
from datetime import datetime, timedelta, timezone

from content_pipeline.build import select_original_trend_candidate


class OriginalReviewQueueTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        self.fresh_feed = {
            "updated_at": self.now.isoformat(),
            "signals": [
                {
                    "provider": "youtube_global_chart",
                    "id": "youtube:tech001",
                    "title": "Which AI is smartest?",
                    "url": "https://www.youtube.com/watch?v=tech001",
                    # Deliberately stale category: the selector must trust title cues.
                    "category": "travel",
                    "chart_category_id": "28",
                    "demand_score": 94.5,
                    "published_at": (self.now - timedelta(hours=5)).isoformat(),
                    "chart_region_count": 4,
                },
                {
                    "provider": "youtube_global_chart",
                    "id": "youtube:animal001",
                    "title": "Why cats suddenly run around the house",
                    "url": "https://www.youtube.com/watch?v=animal001",
                    "category": "animals",
                    "demand_score": 90.1,
                    "published_at": (self.now - timedelta(hours=3)).isoformat(),
                    "chart_region_count": 3,
                },
            ],
        }

    def test_selects_fresh_supported_signal_and_original_script(self):
        candidate, reason = select_original_trend_candidate(
            feed=self.fresh_feed, state={}, now=self.now
        )
        self.assertEqual(reason, "ok")
        self.assertEqual(candidate["source"], "trend_inspired_original")
        self.assertEqual(candidate["story_id"], "ai_photo_video")
        self.assertEqual(candidate["trend_inspiration"]["signal_id"], "youtube:tech001")
        self.assertIn("trend_categories", __import__("content_pipeline.build", fromlist=["CURATED_STORIES"]).CURATED_STORIES[candidate["story_key"]])

    def test_recently_reviewed_story_and_signal_are_skipped(self):
        state = {
            "recent_story_ids": ["ai_photo_video"],
            "recent_signal_ids": ["youtube:tech001"],
        }
        candidate, reason = select_original_trend_candidate(
            feed=self.fresh_feed, state=state, now=self.now
        )
        self.assertEqual(reason, "ok")
        self.assertEqual(candidate["story_id"], "cat_zoomies")
        self.assertEqual(candidate["trend_inspiration"]["signal_id"], "youtube:animal001")

    def test_stale_radar_feed_is_rejected(self):
        feed = dict(self.fresh_feed)
        feed["updated_at"] = (self.now - timedelta(hours=19)).isoformat()
        candidate, reason = select_original_trend_candidate(
            feed=feed, state={}, now=self.now
        )
        self.assertIsNone(candidate)
        self.assertEqual(reason, "trend_radar_feed_stale")

    def test_low_demand_old_or_sensitive_signals_are_not_selected(self):
        feed = dict(self.fresh_feed)
        feed["signals"] = [
            {
                "id": "low",
                "title": "Which AI is smartest?",
                "url": "https://www.youtube.com/watch?v=low",
                "category": "tech",
                "demand_score": 69.9,
                "published_at": (self.now - timedelta(hours=1)).isoformat(),
            },
            {
                "id": "politics",
                "title": "The president discusses election politics",
                "url": "https://www.youtube.com/watch?v=politics",
                "category": "tech",
                "demand_score": 99.0,
                "published_at": (self.now - timedelta(hours=1)).isoformat(),
            },
            {
                "id": "old",
                "title": "Which AI is smartest?",
                "url": "https://www.youtube.com/watch?v=old",
                "category": "tech",
                "demand_score": 99.0,
                "published_at": (self.now - timedelta(days=15)).isoformat(),
            },
        ]
        candidate, reason = select_original_trend_candidate(
            feed=feed, state={}, now=self.now
        )
        self.assertIsNone(candidate)
        self.assertEqual(reason, "no_fresh_supported_trend_category")

    def test_invalid_feed_fails_closed(self):
        candidate, reason = select_original_trend_candidate(
            feed={}, state={}, now=self.now
        )
        self.assertIsNone(candidate)
        self.assertEqual(reason, "trend_radar_feed_invalid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
