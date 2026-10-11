import unittest

from content_pipeline.distribution_plan import create_distribution_plan


class DistributionPlanTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {
            "quality_gate": "passed_publish",
            "script_quality": "passed",
            "duration_seconds": 24.5,
            "script_mode": "curated",
            "content_key": "fixture:one",
            "display_title_fa": "آزمایش",
        }
        self.rights = {
            "automated_publishable": True,
            "assets_cleared": True,
            "script_mode": "curated",
        }

    def test_uncleared_rights_blocks_every_destination(self):
        plan = create_distribution_plan(self.metadata, {
            **self.rights, "automated_publishable": False
        })
        self.assertFalse(plan["rights_gate_passed"])
        self.assertTrue(all(
            row["status"] == "blocked_rights_or_provenance"
            for row in plan["targets"].values()
        ))
        self.assertTrue(all(
            not row["publishing_authorized"]
            for row in plan["targets"].values()
        ))

    def test_quality_failure_blocks_every_destination(self):
        plan = create_distribution_plan(
            {**self.metadata, "script_quality": "review_required"},
            self.rights,
        )
        self.assertFalse(plan["editorial_quality_passed"])
        self.assertTrue(all(
            row["status"] == "blocked_editorial_quality"
            for row in plan["targets"].values()
        ))

    def test_telegram_and_youtube_private_routes_are_opt_in(self):
        plan = create_distribution_plan(self.metadata, self.rights)
        self.assertTrue(plan["rights_gate_passed"])
        self.assertEqual(
            plan["targets"]["telegram"]["status"],
            "opt_in_route_available_not_enabled_by_manifest",
        )
        self.assertEqual(
            plan["targets"]["youtube"]["status"], "private_upload_route_available_opt_in_not_enabled"
        )
        self.assertEqual(
            plan["targets"]["instagram"]["status"], "adapter_not_connected"
        )
        self.assertTrue(all(
            not row["publishing_authorized"]
            for row in plan["targets"].values()
        ))
        self.assertFalse(plan["direct_platform_ad_revenue_is_a_goal"])

    def test_cc_or_unreviewed_script_cannot_pass_rights_gate(self):
        plan = create_distribution_plan(
            {**self.metadata, "script_mode": "ai"},
            {**self.rights, "script_mode": "ai"},
        )
        self.assertFalse(plan["rights_gate_passed"])
        self.assertTrue(all(
            row["status"] == "blocked_rights_or_provenance"
            for row in plan["targets"].values()
        ))


if __name__ == "__main__":
    unittest.main()
