"""Tests for conservative media-rights and publishing gates."""
import unittest

from content_pipeline.publication_rights import (
    assess_asset,
    build_rights_manifest,
    normalize_license,
)


class PublicationRightsTests(unittest.TestCase):
    def test_recognizes_commercially_usable_license_families(self):
        self.assertEqual(normalize_license("CC0 1.0"), "CC0_or_public_domain")
        self.assertEqual(normalize_license("Public domain"), "CC0_or_public_domain")
        self.assertEqual(normalize_license("by"), "CC_BY")
        self.assertEqual(normalize_license("CC BY 4.0"), "CC_BY")
        self.assertEqual(normalize_license("by-sa"), "CC_BY_SA")
        self.assertEqual(normalize_license("CC BY-SA 4.0"), "CC_BY_SA")

    def test_rejects_noncommercial_and_no_derivatives_for_auto_pipeline(self):
        self.assertEqual(normalize_license("CC BY-NC 4.0"), "restricted_or_unclear")
        self.assertEqual(normalize_license("CC BY-ND 4.0"), "restricted_or_unclear")

    def test_cc0_asset_requires_source_record(self):
        missing = assess_asset({"license": "CC0"}, "video")
        cleared = assess_asset({
            "title": "Open clip",
            "license": "CC0 1.0",
            "page_url": "https://example.org/source",
        }, "video")
        self.assertFalse(missing["cleared"])
        self.assertTrue(cleared["cleared"])

    def test_attribution_license_requires_creator_and_license_link(self):
        incomplete = assess_asset({
            "license": "CC BY 4.0",
            "page_url": "https://example.org/source",
        }, "image")
        complete = assess_asset({
            "title": "Open photo",
            "license": "CC BY 4.0",
            "page_url": "https://example.org/source",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "artist": "Example Creator",
        }, "image")
        self.assertEqual(incomplete["status"], "attribution_details_incomplete")
        self.assertTrue(complete["cleared"])
        self.assertTrue(complete["attribution_required"])

    def test_unverified_source_assets_do_not_pass_publishing_gate(self):
        manifest = build_rights_manifest({
            "script_mode": "curated",
            "video_assets": [{
                "title": "Unknown rights clip",
                "license": "unspecified",
                "page_url": "https://example.org/source",
            }],
            "visual_assets": [],
        })
        self.assertFalse(manifest["assets_cleared"])
        self.assertFalse(manifest["automated_publishable"])

    def test_share_alike_asset_requires_compatibility_review(self):
        manifest = build_rights_manifest({
            "script_mode": "curated",
            "video_assets": [],
            "visual_assets": [{
                "title": "Share alike image",
                "license": "CC BY-SA 4.0",
                "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                "artist": "Example Creator",
                "page_url": "https://example.org/source",
            }],
        })
        self.assertFalse(manifest["assets_cleared"])
        self.assertFalse(manifest["automated_publishable"])
        self.assertIn(
            "verify_share_alike_terms_for_the_derivative_video",
            manifest["recommended_action"],
        )

    def test_translated_source_excerpt_does_not_pass_publishing_gate(self):
        manifest = build_rights_manifest({
            "script_mode": "argos_template",
            "video_assets": [],
            "visual_assets": [],
        })
        self.assertTrue(manifest["assets_cleared"])
        self.assertFalse(manifest["automated_publishable"])
        self.assertIn("rewrite_source-derived_script_in_original_words", manifest["recommended_action"])

    def test_curated_original_script_with_no_external_assets_can_pass_gate(self):
        manifest = build_rights_manifest({
            "script_mode": "curated",
            "video_assets": [],
            "visual_assets": [],
        })
        self.assertTrue(manifest["assets_cleared"])
        self.assertTrue(manifest["automated_publishable"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
