"""Offline regression tests for the independent FiLo browser adapter."""
import json
import tempfile
import unittest
from pathlib import Path

from content_pipeline.publish_ifilo import (
    has_prior_attempt,
    load_state,
    make_description,
    make_title,
    normalized_text,
    source_key,
    rights_cleared_for_ifilo,
    _normalize_queue_candidate_url,
    write_state,
)


class FiLoMetadataTests(unittest.TestCase):
    def test_normalized_text_strips_urls_hashtags_and_emoji(self):
        result = normalized_text("😮 یک گربه جالب #حیوانات https://example.org/video")
        self.assertIn("یک گربه جالب", result)
        self.assertNotIn("#", result)
        self.assertNotIn("https://", result)
        self.assertNotIn("😮", result)

    def test_title_is_nonempty_and_concise(self):
        title = make_title({
            "title": "😮 گربه با اسباب‌بازی واکنش خنده‌داری نشان داد #گربه",
            "hook_signals": ["animals"],
        })
        self.assertTrue(title)
        self.assertLessEqual(len(title), 100)
        self.assertNotIn("😮", title)
        self.assertNotIn("#", title)

    def test_title_falls_back_to_description(self):
        title = make_title({"title": "", "description": "یک ترفند کاربردی برای خانه"})
        self.assertIn("ترفند کاربردی", title)

    def test_empty_title_raises_instead_of_inventing_a_caption(self):
        with self.assertRaises(ValueError):
            make_title({"title": "", "description": "", "source_post_caption": ""})

    def test_description_adds_topic_context_and_no_source_url(self):
        description = make_description({
            "title": "واکنش جالب یک گربه",
            "description": "واکنش جالب یک گربه را ببینید https://example.org/p",
            "hook_signals": ["animals", "comedy"],
        }, "لحظه‌ای دیدنی از واکنش جالب یک گربه")
        self.assertIn("موضوع:", description)
        self.assertIn("حیوانات", description)
        self.assertNotIn("https://", description)


class FiLoNasaRightsGateTests(unittest.TestCase):
    def test_nasa_candidate_requires_official_policy_and_screen_metadata(self):
        valid = {
            "source": "nasa_public_domain_video",
            "rights_cleared_for_publication": True,
            "rights_evidence_url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
            "license_url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
            "rights_screen": {
                "basis": "metadata_passed_conservative_nasa_screen",
                "center": "NASA",
                "secondary_creator": "",
                "third_party_warning_found": False,
                "person_or_audio_signal_found": False,
            },
        }
        self.assertTrue(rights_cleared_for_ifilo(valid))
        jpl_valid = dict(valid)
        jpl_valid["rights_screen"] = dict(valid["rights_screen"], center="JPL", secondary_creator="NASA")
        self.assertTrue(rights_cleared_for_ifilo(jpl_valid))
        unverified = dict(valid)
        unverified["rights_evidence_url"] = "https://example.com/license"
        self.assertFalse(rights_cleared_for_ifilo(unverified))
        missing_screen = dict(valid)
        missing_screen.pop("rights_screen")
        self.assertFalse(rights_cleared_for_ifilo(missing_screen))


class FiLoQueueSourceTests(unittest.TestCase):
    def test_nasa_candidate_url_requires_official_detail_host(self):
        item = {"source": "nasa_public_domain_video"}
        self.assertEqual(
            _normalize_queue_candidate_url("https://images.nasa.gov/details/ABC_123", item),
            "https://images.nasa.gov/details/ABC_123",
        )
        self.assertEqual(_normalize_queue_candidate_url("https://example.com/details/ABC_123", item), "")
        self.assertEqual(_normalize_queue_candidate_url("http://images.nasa.gov/details/ABC_123", item), "")

    def test_nasa_description_includes_source_and_reuse_guidance(self):
        item = {
            "source": "nasa_public_domain_video",
            "title": "Earth at night from orbit",
            "description": "Earth observation footage.",
            "url": "https://images.nasa.gov/details/NASA001",
            "rights_evidence_url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
            "hook_signals": ["science_curiosity"],
        }
        description = make_description(item, "لحظه‌ای دیدنی از زمین")
        self.assertIn("منبع ویدئو: NASA", description)
        self.assertIn("https://images.nasa.gov/details/NASA001", description)
        self.assertIn("https://www.nasa.gov/nasa-brand-center/images-and-media/", description)


class FiLoRightsTests(unittest.TestCase):
    def test_rights_evidence_requires_explicit_clearance_and_a_reference(self):
        self.assertFalse(rights_cleared_for_ifilo({"rights_evidence_url": "https://example.org/license"}))
        self.assertFalse(rights_cleared_for_ifilo({"rights_cleared_for_publication": True}))
        self.assertFalse(rights_cleared_for_ifilo({"rights_cleared_for_publication": False, "license_url": "https://example.org/license"}))
        self.assertTrue(rights_cleared_for_ifilo({
            "rights_cleared_for_publication": True,
            "license_url": "https://example.org/license",
        }))


class FiLoDeduplicationTests(unittest.TestCase):
    def test_source_key_is_stable_and_nonempty(self):
        row = {"url": "https://t.me/demo/123"}
        self.assertEqual(source_key(row), source_key(dict(row)))
        self.assertNotEqual(source_key(row), source_key({"url": "https://t.me/demo/124"}))

    def test_prior_attempt_is_detected_by_key_or_url(self):
        row = {"id": "demo-1", "url": "https://t.me/demo/123"}
        state = {"attempts": [{"source_key": source_key(row), "source_url": row["url"], "status": "uploaded"}]}
        self.assertTrue(has_prior_attempt(row, state))

    def test_unknown_candidate_is_not_duplicate(self):
        self.assertFalse(has_prior_attempt({"url": "https://t.me/demo/123"}, {"attempts": []}))

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ifilo_upload_state.json"
            state = {"schema_version": 1, "attempts": [{"source_key": "abc", "status": "pending"}]}
            write_state(state, path)
            loaded = load_state(path)
            self.assertEqual(loaded["attempts"][0]["source_key"], "abc")
            self.assertIn("updated_at", loaded)


if __name__ == "__main__":
    unittest.main()
