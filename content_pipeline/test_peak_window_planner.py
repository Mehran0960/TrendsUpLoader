"""Tests for peak-window-aware discovery preparation."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from content_pipeline.peak_window_planner import (
    build_plan, count_qualified_review_candidates, refresh_candidate_ages,
    upcoming_windows,
)


class PeakWindowPlannerTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "timezone": "Asia/Tehran",
            "preparation_ahead_minutes": 300,
            "review_queue_target": 6,
            "max_discovery_runs_per_local_day": 6,
            "windows": {
                "instagram_reels": ["18:00", "21:00"],
                "youtube_shorts": ["12:00", "20:30"],
                "aparat": ["18:30"],
            },
        }
        self.now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)

    def test_planner_lists_multiple_windows_for_each_platform(self):
        windows = upcoming_windows(self.now, self.config)
        platforms = {row["platform"] for row in windows}
        self.assertEqual(platforms, {"instagram_reels", "youtube_shorts", "aparat"})
        self.assertGreaterEqual(len(windows), 4)
        self.assertEqual(windows, sorted(windows, key=lambda row: (row["minutes_until"], row["platform"])))

    def test_empty_review_queue_triggers_preparation_before_near_window(self):
        plan = build_plan(self.now, self.config, 0, {"version": 1, "local_day": "2026-10-11", "discovery_runs": 0})
        self.assertTrue(plan["should_discover"])
        self.assertEqual(plan["reason"], "replenish_review_queue_before_platform_window")

    def test_sufficient_queue_skips_discovery_even_when_window_is_near(self):
        plan = build_plan(self.now, self.config, 6, {"version": 1, "local_day": "2026-10-11", "discovery_runs": 0})
        self.assertFalse(plan["should_discover"])
        self.assertEqual(plan["reason"], "review_queue_has_enough_candidates")

    def test_queue_low_but_no_window_inside_prep_lead_skips(self):
        config = {**self.config, "windows": {"youtube_shorts": ["23:50"]}}
        plan = build_plan(self.now, config, 0, {"version": 1, "local_day": "2026-10-11", "discovery_runs": 0})
        self.assertFalse(plan["should_discover"])
        self.assertEqual(plan["reason"], "next_platform_window_not_close_enough")

    def test_daily_discovery_cap_wins_over_force(self):
        plan = build_plan(
            self.now, self.config, 0,
            {"version": 1, "local_day": "2026-10-11", "discovery_runs": 6},
            force=True,
        )
        self.assertFalse(plan["should_discover"])
        self.assertEqual(plan["reason"], "daily_discovery_cap_reached")

    def test_manual_dispatch_can_force_a_sufficient_queue_before_window(self):
        plan = build_plan(
            self.now, self.config, 6,
            {"version": 1, "local_day": "2026-10-11", "discovery_runs": 0},
            force=True,
        )
        self.assertTrue(plan["should_discover"])
        self.assertEqual(plan["reason"], "manual_dispatch")

    def test_stored_candidate_age_increases_while_discovery_is_paused(self):
        now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
        items = [{
            "url": "https://example.com/clip",
            "age_hours": 2,
            "discovered_at": "2026-10-11T09:00:00+00:00",
        }]
        self.assertEqual(refresh_candidate_ages(items, now)[0]["age_hours"], 5.0)

    def test_candidate_count_uses_existing_selection_gates(self):
        data = {"items": [{"url": "https://example.com/a"}]}
        state = {"media_reviewed_ids": [], "media_reviewed_urls": []}
        with patch("content_pipeline.peak_window_planner.select_media_candidates", return_value=[{}, {}]) as select:
            count = count_qualified_review_candidates(self.now, data, state)
        self.assertEqual(count, 2)
        select.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
