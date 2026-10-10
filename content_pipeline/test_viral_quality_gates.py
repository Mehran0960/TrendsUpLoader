"""Deterministic smoke tests for short-form viral quality gates."""
import unittest

from content_pipeline.iran_video_hunter import (
    COMPILATION_TITLE_RE,
    content_shareability_proxy,
    single_payoff_gate,
    telegram_tech_exploration_candidate,
)


class ViralQualityGateTests(unittest.TestCase):
    def test_detects_multi_story_roundup_titles(self):
        for title in (
            "روایت سه ماجرا",
            "گلچین اتفاقات عجیب",
            "Compilation of funny moments",
        ):
            with self.subTest(title=title):
                self.assertIsNotNone(COMPILATION_TITLE_RE.search(title))

    def test_rejects_multi_story_title_without_exceptional_hook(self):
        cq = {
            "hook_first_event_score": 57.0,
            "hook_event_score": 59.0,
            "hook_structure_score": 45.0,
        }
        gate = single_payoff_gate(
            {"title": "روایت سه ماجرا"},
            {"duration": 42.0},
            cq,
            demand=58.0,
            attraction=60.0,
        )
        self.assertFalse(gate["passed"])
        self.assertIn(
            "multi_story_or_roundup_title_without_exceptional_hook",
            gate["reasons"],
        )

    def test_rejects_long_routine_clip(self):
        gate = single_payoff_gate(
            {"title": "لحظه‌ای باورنکردنی"},
            {"duration": 96.0},
            {
                "hook_first_event_score": 60.0,
                "hook_event_score": 61.0,
                "hook_structure_score": 43.0,
            },
            demand=65.0,
            attraction=66.0,
        )
        self.assertFalse(gate["passed"])
        self.assertIn("longer_than_shareable_short_form", gate["reasons"])

    def test_exceptional_single_payoff_can_survive_roundup_and_length_gate(self):
        gate = single_payoff_gate(
            {"title": "روایت سه ماجرا"},
            {"duration": 96.0},
            {
                "hook_first_event_score": 75.0,
                "hook_event_score": 78.0,
                "hook_structure_score": 65.0,
            },
            demand=91.0,
            attraction=82.0,
        )
        self.assertTrue(gate["passed"])
        self.assertTrue(gate["exceptional_hook_exception"])

    def test_technology_cue_contributes_to_shareability_discovery(self):
        score, flags = content_shareability_proxy({
            "title": "این هوش مصنوعی یک عکس را به ویدئوی واقعی تبدیل می‌کند 🤯",
            "description": "",
        })
        self.assertGreaterEqual(score, 48.0)
        self.assertGreater(flags["technology_cue_count"], 0)

    def test_ai_demo_can_enter_the_exploration_lane(self):
        self.assertTrue(telegram_tech_exploration_candidate({
            "source": "telegram_native_video",
            "views": 7200,
            "age_hours": 12,
            "title": "ساخت بازی با چند خط پرامپت و هوش مصنوعی",
            "description": "ویدئو نشان می‌دهد مدل چطور بازی را می‌سازد",
        }))

    def test_ai_news_without_a_demo_does_not_enter_exploration_lane(self):
        self.assertFalse(telegram_tech_exploration_candidate({
            "source": "telegram_native_video",
            "views": 50000,
            "age_hours": 6,
            "title": "معرفی مدل جدید هوش مصنوعی",
            "description": "خبر معرفی مدل تازه",
        }))

    def test_english_ai_demo_can_enter_exploration_lane(self):
        self.assertTrue(telegram_tech_exploration_candidate({
            "source": "telegram_native_video",
            "views": 7200,
            "age_hours": 12,
            "title": "AI turns a photo into a realistic video",
            "description": "A short demo showing the generated video",
        }))

    def test_tech_exploration_requires_freshness_and_reach(self):
        base = {
            "source": "telegram_native_video",
            "views": 7200,
            "age_hours": 12,
            "title": "تبدیل عکس به ویدئو با هوش مصنوعی",
            "description": "نمایش نتیجهٔ ساخت ویدئوی واقعی",
        }
        old = dict(base, age_hours=80)
        low_view = dict(base, views=2500)
        self.assertFalse(telegram_tech_exploration_candidate(old))
        self.assertFalse(telegram_tech_exploration_candidate(low_view))


if __name__ == "__main__":
    unittest.main(verbosity=2)
