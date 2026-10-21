"""Public synchronization boundaries."""
import unittest
from core.settings_store import DEFAULT_SETTINGS, resolve_provider_model
from routers.settings import SECRET_FIELDS
from main import app

class PublicSyncPrivacyTests(unittest.TestCase):
    def test_public_routes_and_defaults(self):
        paths={getattr(route, 'path', '') for route in app.routes}
        self.assertNotIn('/api/auth/sso', paths)
        self.assertNotIn('/api/auth/exchange', paths)
        self.assertFalse(any('/micadb/' in path for path in paths))
        self.assertTrue({'/api/ios-host/pair', '/api/ios-host/pairings', '/api/ios-local/connect',
                         '/api/automation/cases/workspace', '/v1/providers/ios-bridge',
                         '/v1/providers/ios-host'}.issubset(paths))
        self.assertEqual(('openai', 'gpt-4o'), resolve_provider_model({}))
        self.assertEqual('https://ark.cn-beijing.volces.com/api/v3', DEFAULT_SETTINGS['ark_base_url'])

    def test_cli_download_source_is_available(self):
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            response = client.get('/api/app/cli/download')
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.content.startswith(b'#!/usr/bin/env python3'))

    def test_provider_secret_masking_is_preserved(self):
        self.assertTrue({'openai_api_key','anthropic_api_key','gemini_api_key','zhipu_api_key',
                         'groq_api_key','ark_api_key','aws_access_key_id','aws_secret_access_key',
                         'webhook_url'}.issubset(SECRET_FIELDS))

    def test_native_helper_has_no_preconfigured_server(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        source = (root / 'tools/macos/SABDeviceHelper.swift').read_text()
        self.assertIn('@Published var server = ""', source)
        self.assertIn("io.github.rejigtian.smart-ai-bot.device-helper",
                      (root / 'tools/macos/build.py').read_text())

    def test_android_client_contains_recording_video_and_real_stop(self):
        from pathlib import Path
        source = Path(__file__).resolve().parents[2] / 'android/app/src/main/java/com/dream/smart_androidbot'
        dispatcher = (source / 'service/ActionDispatcher.kt').read_text()
        for method in ('"start_record"', '"stop_record"', '"video/start"', '"video/stop"'):
            self.assertIn(method, dispatcher)
        self.assertIn('service.forceStopAppViaSettings(packageName)', (source / 'api/ApiHandler.kt').read_text())
        self.assertTrue((source / 'service/LiveVideoService.kt').is_file())
