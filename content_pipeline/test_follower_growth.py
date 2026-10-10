"""Tests for follower-growth-first discovery scoring."""
import unittest

from content_pipeline.follower_growth import (
    annotate_candidate,
    expected_platform_for_query,
    rank_candidates,
)


class FollowerGrowthTests(unittest.TestCase):
    def test_fresh_shareable_video_outranks_stale_hard_news(self):
        rows = [
            {
                "url": "https://www.instagram.com/reel/news123/",
                "platform": "instagram",
                "source": "telegram_native_video",
                "title": "گزارش حمله نظامی و انفجار در منطقه",
                "views": 900000,
                "age_hours": 90,
                "discovered_at": "2026-10-10T10:00:00+00:00",
            },
            {
                "url": "https://www.instagram.com/reel/fun123/",
                "platform": "instagram",
                "source": "telegram_native_video",
                "title": "یه شوخی خنده‌دار؛ آخرش غافلگیر می‌شی!",
                "views": 80000,
                "age_hours": 2,
                "discovered_at": "2026-10-10T10:00:00+00:00",
            },
        ]
        ranked = rank_candidates(rows)
        self.assertEqual(ranked[0]["url"], "https://www.instagram.com/reel/fun123/")
        self.assertGreater(ranked[0]["follow_growth_score"], ranked[1]["follow_growth_score"])
        self.assertEqual(ranked[0]["candidate_action"], "prioritize_for_visual_and_rights_review")
        self.assertEqual(ranked[0]["rights_status"], "not_assessed")
        self.assertEqual(ranked[0]["rights_status"], "not_assessed")

    def test_unknown_metrics_are_low_confidence_and_never_auto_publishable(self):
        result = annotate_candidate({
            "url": "https://www.tiktok.com/@example/video/12345",
            "platform": "tiktok",
            "title": "گربه‌ای که یک حرکت عجیب انجام داد",
            "description": "ویدئوی بامزه",
        })
        self.assertEqual(result["score_confidence"], "low")
        self.assertEqual(result["candidate_action"], "manual_review")
        self.assertEqual(result["rights_status"], "not_assessed")
        self.assertIn("animals", result["hook_signals"])

    def test_sensitive_risk_is_rejected_for_review(self):
        result = annotate_candidate({
            "url": "https://www.youtube.com/shorts/abcdef",
            "platform": "youtube",
            "title": "ویدئوی خون و خشونت شدید",
            "views": 100000,
            "age_hours": 1,
        })
        self.assertEqual(result["candidate_action"], "reject_sensitive_or_manual_safety_review")

    def test_search_domain_mismatch_can_be_filtered(self):
        self.assertEqual(expected_platform_for_query('site:instagram.com/reel/ funny video'), "instagram")
        self.assertEqual(expected_platform_for_query('site:youtube.com/shorts amazing'), "youtube")
        self.assertEqual(expected_platform_for_query('cute animals viral video'), "")

    def test_actual_interactions_raise_confidence(self):
        result = annotate_candidate({
            "url": "https://www.youtube.com/shorts/abcdef",
            "platform": "youtube",
            "title": "ترفند عجیب و باورنکردنی",
            "views": 50000,
            "likes": 2500,
            "comments": 150,
            "age_hours": 4,
        })
        self.assertEqual(result["score_confidence"], "high")
        self.assertIn(result["candidate_action"], {"prioritize_for_visual_and_rights_review", "manual_review"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
