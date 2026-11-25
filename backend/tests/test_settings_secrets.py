"""Explicit credential deletion must preserve blank-input and tenant semantics."""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request
from routers import settings


class SettingsSecretTests(unittest.IsolatedAsyncioTestCase):
    def request(self, role="owner"):
        return Request({"type": "http", "state": {"account_id": "test-account", "role": role}})

    async def test_delete_and_read_back_without_touching_other_settings(self):
        stored = {"webhook_url": "https://example.invalid/hook", "language": "en"}

        async def read(account):
            self.assertEqual(account, "test-account")
            return dict(stored)

        async def write(account, data):
            self.assertEqual(account, "test-account")
            stored.clear()
            stored.update(data)

        with patch.object(settings, "load_settings", side_effect=read), patch.object(settings, "save_settings", side_effect=write):
            result = await settings.update_settings(settings.SettingsUpdate(clear_secrets=["webhook_url"]), self.request())
            reread = await settings.get_settings(self.request())
        self.assertEqual(stored["webhook_url"], "")
        self.assertEqual(stored["language"], "en")
        self.assertNotIn("webhook_url", reread.configured_secrets)
        self.assertEqual(reread.webhook_url, "")
        self.assertEqual(result.webhook_url, "")

    async def test_blank_keeps_key_and_deletion_wins_over_new_input(self):
        with patch.object(settings, "load_settings", AsyncMock(return_value={"webhook_url": "https://example.invalid/old"})), patch.object(settings, "save_settings", AsyncMock()) as save:
            await settings.update_settings(settings.SettingsUpdate(webhook_url=""), self.request())
            self.assertEqual(save.call_args.args[1]["webhook_url"], "https://example.invalid/old")
            await settings.update_settings(settings.SettingsUpdate(webhook_url="https://example.invalid/new", clear_secrets=["webhook_url"]), self.request())
            self.assertEqual(save.call_args.args[1]["webhook_url"], "")
            self.assertNotIn("clear_secrets", save.call_args.args[1])

    async def test_reject_unknown_field_and_member_before_write(self):
        with patch.object(settings, "save_settings", AsyncMock()) as save:
            with self.assertRaises(HTTPException) as error:
                await settings.update_settings(settings.SettingsUpdate(clear_secrets=["default_model_id"]), self.request())
            self.assertEqual(error.exception.status_code, 422)
            with self.assertRaises(HTTPException) as error:
                await settings.update_settings(settings.SettingsUpdate(clear_secrets=["webhook_url"]), self.request("member"))
            self.assertEqual(error.exception.status_code, 403)
            save.assert_not_awaited()
