"""Tests for freshness-aware multi-platform candidate retention."""
import unittest

from content_pipeline.social_discovery import sort_discovery_items


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

    def test_limit_is_respected(self):
        rows = [
            {"url": f"https://example.com/{i}", "discovered_at": f"2026-10-10T10:{i:02d}:00+00:00"}
            for i in range(10)
        ]
        self.assertEqual(len(sort_discovery_items(rows, limit=3)), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
