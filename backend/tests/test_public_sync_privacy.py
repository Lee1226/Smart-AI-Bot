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
        self.assertEqual(('openai', 'gpt-4o'), resolve_provider_model({}))
        self.assertEqual('https://ark.cn-beijing.volces.com/api/v3', DEFAULT_SETTINGS['ark_base_url'])

    def test_provider_secret_masking_is_preserved(self):
        self.assertTrue({'openai_api_key','anthropic_api_key','gemini_api_key','zhipu_api_key',
                         'groq_api_key','ark_api_key','aws_access_key_id','aws_secret_access_key',
                         'webhook_url'}.issubset(SECRET_FIELDS))
