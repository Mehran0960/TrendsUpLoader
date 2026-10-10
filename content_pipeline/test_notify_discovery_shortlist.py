"""Tests for concise and safe candidate notifications."""
import unittest
from datetime import datetime, timezone

from content_pipeline.notify_discovery_shortlist import render_message, select_shortlist


class DiscoveryShortlistTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

    def candidate(self, ident, **overrides):
        item = {
            "id": ident,
            "url": f"https://t.me/example/{ident}",
            "title": f"Video {ident}",
            "platform": "telegram",
            "views": 25000,
            "age_hours": 2,
            "follow_growth_score": 57,
            "score_confidence": "medium",
            "news_risk_signal": False,
            "sensitive_risk_signal": False,
        }
        item.update(overrides)
        return item

    def test_selects_only_recent_new_review_leads(self):
        rows = [
            self.candidate("already-sent"),
            self.candidate("old", age_hours=90),
            self.candidate("news", news_risk_signal=True),
            self.candidate("sensitive", sensitive_risk_signal=True),
            self.candidate("roundup", compilation_signal=True),
            self.candidate("weapons", safety_review_signal=True),
            self.candidate("unknown", score_confidence="low"),
            self.candidate("new-good"),
        ]
        selected = select_shortlist(rows, {"already-sent"}, self.now)
        self.assertEqual([x["id"] for x in selected], ["new-good"])

    def test_shortlist_caps_at_three(self):
        rows = [self.candidate(str(i)) for i in range(8)]
        self.assertEqual(len(select_shortlist(rows, now=self.now)), 3)

    def test_message_marks_leads_as_unverified(self):
        text = render_message([self.candidate("1", title="<script>alert(1)</script>")])
        self.assertIn("سرنخ‌اند", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("<script>", text)
        self.assertIn("مشاهدهٔ منبع", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
