import unittest

from content_pipeline.notify_discovery_shortlist import select_shortlist


def candidate(ident, platform, score, age=2):
    return {
        "id": ident,
        "url": f"https://example.com/{ident}",
        "platform": platform,
        "follow_growth_score": score,
        "score_confidence": "high",
        "age_hours": age,
    }


class DiscoveryShortlistTests(unittest.TestCase):
    def test_first_three_slots_are_diverse_when_eligible_platforms_exist(self):
        items = [
            candidate("yt-1", "youtube", 92),
            candidate("yt-2", "youtube", 90),
            candidate("ig-1", "instagram", 87),
            candidate("tt-1", "tiktok", 80),
        ]
        selected = select_shortlist(items)
        self.assertEqual([x["id"] for x in selected], ["yt-1", "ig-1", "tt-1"])
        self.assertEqual(len({x["platform"] for x in selected}), 3)

    def test_shortlist_fills_remaining_slots_when_only_one_platform_exists(self):
        items = [
            candidate("ig-1", "instagram", 92),
            candidate("ig-2", "instagram", 90),
            candidate("ig-3", "instagram", 88),
            candidate("ig-4", "instagram", 80),
        ]
        selected = select_shortlist(items)
        self.assertEqual([x["id"] for x in selected], ["ig-1", "ig-2", "ig-3"])

    def test_diversity_does_not_bypass_existing_safety_gates(self):
        items = [
            candidate("yt-risk", "youtube", 99),
            {**candidate("ig-risk", "instagram", 97), "news_risk_signal": True},
            candidate("tt-ok", "tiktok", 70),
        ]
        selected = select_shortlist(items)
        self.assertEqual([x["id"] for x in selected], ["yt-risk", "tt-ok"])


if __name__ == "__main__":
    unittest.main()
