import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from main import app


class ManualSuiteTests(unittest.TestCase):
    def test_create_edit_and_account_isolation(self):
        with patch.dict('os.environ', {'SAB_ALLOW_PUBLIC_REGISTRATION': 'true'}), TestClient(app) as client:
            headers = []
            for _ in range(2):
                suffix = uuid.uuid4().hex
                response = client.post('/api/auth/register', json={
                    'account_name': suffix, 'email': suffix + '@example.test',
                    'password': 'test-only-password', 'name': 'Manual suite test'})
                self.assertEqual(201, response.status_code, response.text)
                headers.append({'Authorization': 'Bearer ' + response.json()['access_token']})
            for name in ['', '   ', 'x' * 121]:
                self.assertEqual(422, client.post('/api/suites/manual', json={'name': name}, headers=headers[0]).status_code)
            response = client.post('/api/suites/manual', json={
                'name': '  手动用例  ', 'app_package': ' com.android.settings ', 'tag': ' 回归 '}, headers=headers[0])
            self.assertEqual(201, response.status_code, response.text)
            suite = response.json()
            self.assertEqual(('手动用例', 'manual', 0, 'com.android.settings', '回归'),
                             (suite['name'], suite['source_format'], suite['case_count'], suite['app_package'], suite['tag']))
            path = '/api/suites/' + suite['id']
            self.assertEqual([], client.get(path + '/nodes', headers=headers[0]).json())
            node = client.post(path + '/nodes', json={'action': '打开设置', 'expected': '显示设置标题'}, headers=headers[0])
            self.assertEqual(201, node.status_code, node.text)
            self.assertEqual('显示设置标题', client.get(path + '/nodes', headers=headers[0]).json()[0]['expected'])
            self.assertEqual(1, client.get(path, headers=headers[0]).json()['case_count'])
            self.assertEqual(1, client.get('/api/suites', headers=headers[0]).json()['items'][0]['case_count'])
            self.assertEqual(1, client.get('/api/suites', headers=headers[0]).json()['total'])
            self.assertEqual(0, client.get('/api/suites', headers=headers[1]).json()['total'])
            self.assertIn(client.get(path, headers=headers[1]).status_code, (403, 404))
            self.assertIn(client.post(path + '/nodes', json={'action': 'other'}, headers=headers[1]).status_code, (403, 404))
