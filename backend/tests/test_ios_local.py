import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from routers.ios_local import guard

class GuardTest(unittest.TestCase):
    def request(self, host='localhost', client='127.0.0.1', origin='http://localhost:8000', account='local'):
        return SimpleNamespace(url=SimpleNamespace(hostname=host), client=SimpleNamespace(host=client),
                               headers={'origin': origin}, base_url='http://localhost:8000/', state=SimpleNamespace(account_id=account))

    @patch('routers.ios_local.platform.system', return_value='Darwin')
    @patch.dict('os.environ', {'SAB_IOS_LOCAL_ENABLED': '1'})
    def test_reject_remote_and_cross_origin(self, _):
        guard(self.request())
        for r in [self.request(host='evil.test'), self.request(client='10.1.1.1'), self.request(origin='https://evil.test'), self.request(account=None)]:
            with self.assertRaises(HTTPException): guard(r)

    @patch.dict('os.environ', {'SAB_IOS_LOCAL_ENABLED': '0'})
    def test_disabled_by_default(self):
        with self.assertRaises(HTTPException): guard(self.request())
