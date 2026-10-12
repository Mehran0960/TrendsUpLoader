"""Tests for native Telegram transfer routing and public-source allowlists."""
import unittest

from content_pipeline.telegram_native_relay import audience_republication_allowed


class TelegramNativeRelayPolicyTests(unittest.TestCase):
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
