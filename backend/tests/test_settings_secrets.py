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
        stored = {"ark_api_key": "fake-ark", "openai_api_key": "fake-openai", "default_model": "custom-model"}

        async def read(account):
            self.assertEqual(account, "test-account")
            return dict(stored)

        async def write(account, data):
            self.assertEqual(account, "test-account")
            stored.clear()
            stored.update(data)

        with patch.object(settings, "load_settings", side_effect=read), patch.object(settings, "save_settings", side_effect=write):
            result = await settings.update_settings(settings.SettingsUpdate(clear_secrets=["ark_api_key"]), self.request())
            reread = await settings.get_settings(self.request())
        self.assertEqual(stored["ark_api_key"], "")
        self.assertEqual(stored["openai_api_key"], "fake-openai")
        self.assertEqual(stored["default_model"], "custom-model")
        self.assertNotIn("ark_api_key", reread.configured_secrets)
        self.assertIn("openai_api_key", reread.configured_secrets)
        self.assertEqual(result.openai_api_key, "")

    async def test_blank_keeps_key_and_deletion_wins_over_new_input(self):
        with patch.object(settings, "load_settings", AsyncMock(return_value={"ark_api_key": "fake-old"})), patch.object(settings, "save_settings", AsyncMock()) as save:
            await settings.update_settings(settings.SettingsUpdate(ark_api_key=""), self.request())
            self.assertEqual(save.call_args.args[1]["ark_api_key"], "fake-old")
            await settings.update_settings(settings.SettingsUpdate(ark_api_key="fake-new", clear_secrets=["ark_api_key"]), self.request())
            self.assertEqual(save.call_args.args[1]["ark_api_key"], "")
            self.assertNotIn("clear_secrets", save.call_args.args[1])

    async def test_reject_unknown_field_and_member_before_write(self):
        with patch.object(settings, "save_settings", AsyncMock()) as save:
            with self.assertRaises(HTTPException) as error:
                await settings.update_settings(settings.SettingsUpdate(clear_secrets=["default_model"]), self.request())
            self.assertEqual(error.exception.status_code, 422)
            with self.assertRaises(HTTPException) as error:
                await settings.update_settings(settings.SettingsUpdate(clear_secrets=["ark_api_key"]), self.request("member"))
            self.assertEqual(error.exception.status_code, 403)
            save.assert_not_awaited()
