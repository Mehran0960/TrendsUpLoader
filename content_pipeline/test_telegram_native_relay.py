"""Tests for native Telegram transfer routing and public-source allowlists."""
import unittest

from content_pipeline.telegram_native_relay import allowed_source, audience_republication_allowed, destination_token_for


class TelegramNativeRelayPolicyTests(unittest.TestCase):
    def test_public_route_reuses_existing_bot_when_no_separate_audience_bot_exists(self):
        self.assertEqual(destination_token_for(True, "private-token", ""), "private-token")

    def test_public_route_prefers_dedicated_audience_bot_when_configured(self):
        self.assertEqual(destination_token_for(True, "private-token", "public-token"), "public-token")

    def test_private_review_always_uses_private_bot(self):
        self.assertEqual(destination_token_for(False, "private-token", "public-token"), "private-token")

    def test_bot_receivable_channel_is_private_review_eligible_without_public_authorization(self):
        chat = {"id": "-100123", "username": "example_source"}
        policy = {
            "allow_all_channels_bot_can_receive": True,
            "authorized_public_repost_usernames": [],
            "authorized_public_repost_chat_ids": [],
        }
        self.assertTrue(allowed_source(chat, policy))
        self.assertFalse(audience_republication_allowed(chat, policy))

    def test_private_review_intake_is_default_deny_when_unconfigured(self):
        chat = {"id": "-100123", "username": "example_source"}
        self.assertFalse(allowed_source(chat, {}))

    def test_public_republication_is_denied_by_default(self):
        chat = {"id": "-100123", "username": "example_source"}
        self.assertFalse(audience_republication_allowed(chat, {}))

    def test_private_review_allowlist_does_not_grant_public_republication(self):
        chat = {"id": "-100123", "username": "example_source"}
        policy = {
            "allowed_usernames": ["example_source"],
            "allowed_chat_ids": ["-100123"],
            "authorized_public_repost_usernames": [],
            "authorized_public_repost_chat_ids": [],
        }
        self.assertFalse(audience_republication_allowed(chat, policy))

    def test_explicit_public_username_allowlist_allows_native_transfer(self):
        chat = {"id": "-100123", "username": "Example_Source"}
        policy = {"authorized_public_repost_usernames": ["@example_source"]}
        self.assertTrue(audience_republication_allowed(chat, policy))

    def test_explicit_public_chat_id_allowlist_allows_native_transfer(self):
        chat = {"id": "-100123", "username": "source_without_username"}
        policy = {"authorized_public_repost_chat_ids": ["-100123"]}
        self.assertTrue(audience_republication_allowed(chat, policy))

    def test_unlisted_source_never_uses_public_destination(self):
        policy = {"authorized_public_repost_usernames": ["authorized_channel"]}
        self.assertFalse(
            audience_republication_allowed(
                {"id": "-100987", "username": "different_channel"}, policy
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
