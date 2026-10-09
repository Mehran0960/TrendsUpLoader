"""Deterministic smoke tests for short-form viral quality gates."""
import unittest

from content_pipeline.iran_video_hunter import (
    COMPILATION_TITLE_RE,
    content_shareability_proxy,
    single_payoff_gate,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
