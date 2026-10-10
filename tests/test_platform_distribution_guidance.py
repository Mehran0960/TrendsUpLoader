import unittest

from content_pipeline.follower_growth import annotate_candidate


class PlatformDistributionGuidanceTests(unittest.TestCase):
    def test_platform_specific_routes_are_kept_separate(self):
        item = annotate_candidate({
            "url": "https://www.instagram.com/reel/example/",
            "title": "Unexpected animal moment",
            "description": "A short clip with a clear payoff",
            "platform": "instagram",
            "source": "public_search",
        })
        guidance = item["distribution_guidance"]
        for platform in ("instagram", "tiktok", "youtube", "telegram", "aparat", "x"):
            self.assertIn(platform, guidance)
            self.assertTrue(guidance[platform]["rights_must_be_checked_for_file_reuse"])
            self.assertFalse(guidance[platform]["publishing_authorized"])

        self.assertIn("native_repost", guidance["instagram"]["preferred_route"])
        self.assertEqual(guidance["youtube"]["publishing_automation"], "not_connected; export bundle only")
        self.assertIn("approval and audit", guidance["tiktok"]["publishing_automation"])

    def test_guidance_does_not_claim_rights_or_force_one_global_destination(self):
        item = annotate_candidate({
            "url": "https://example.org/video",
            "title": "Amazing transformation",
            "platform": "web",
            "source": "public_search",
        })
        guidance = item["distribution_guidance"]
        self.assertEqual(set(guidance), {"instagram", "tiktok", "youtube", "telegram", "aparat", "x"})
        self.assertEqual(item["rights_status"], "not_assessed")
        self.assertTrue(all(not v["publishing_authorized"] for v in guidance.values()))


if __name__ == "__main__":
    unittest.main()
