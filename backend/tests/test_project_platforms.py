import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import projects


class ProjectPlatformTests(unittest.TestCase):
    def setUp(self):
        self.allowlist = patch.dict('os.environ', {'SAB_KB_SEARCH_COMMAND_ALLOWLIST': 'kb-search'})
        self.allowlist.start()
        self.addCleanup(self.allowlist.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mock = patch.object(projects, 'PROJECTS_PATH', Path(self.temp.name) / 'projects.json')
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.project = projects.new_project({
            'name': '示例项目', 'app_package': 'com.example.app',
            'app_ids': {'harmony': 'com.example.harmony'},
            'kb_path': self.temp.name, 'kb_search_cmd': 'kb-search',
        })
        projects.save_projects([self.project])

    def test_same_project_distinct_platform_targets_shared_knowledge(self):
        suite = SimpleNamespace(project_id=self.project['id'], app_package='wrong.old.package')
        self.assertEqual('com.example.app', projects.target_for_suite(suite, 'android'))
        self.assertEqual('com.example.harmony', projects.target_for_suite(suite, 'harmony'))
        self.assertEqual([self.temp.name], projects.kb_roots_for(suite.project_id))
        self.assertEqual('kb-search', projects.kb_search_cmd_for(suite.project_id))
        with self.assertRaisesRegex(ValueError, 'ios'):
            projects.target_for_suite(suite, 'ios')

    def test_legacy_package_resolves_project_across_platforms(self):
        suite = SimpleNamespace(project_id='', app_package='com.example.app')
        self.assertEqual('com.example.harmony', projects.target_for_suite(suite, 'harmony'))

    def test_deleted_explicit_project_never_falls_back(self):
        with self.assertRaisesRegex(ValueError, '已删除'):
            projects.target_for_suite(SimpleNamespace(project_id='deleted', app_package='com.example.app'), 'android')

    def test_legacy_standalone_android_only(self):
        suite = SimpleNamespace(project_id='', app_package='com.example.standalone')
        self.assertEqual('com.example.standalone', projects.target_for_suite(suite, 'android'))
        with self.assertRaisesRegex(ValueError, 'Android'):
            projects.target_for_suite(suite, 'harmony')

    def test_old_profile_migration_keeps_id(self):
        projects.save_projects([{'id': 'legacy', 'name': 'old', 'app_package': 'old.android'}])
        p = projects.load_projects()[0]
        self.assertEqual(('legacy', {'android': 'old.android'}), (p['id'], p['app_ids']))

class ProjectAssignmentApiTests(unittest.TestCase):
    def test_assignment_roundtrip_and_tenant_isolation(self):
        import os
        import uuid
        from fastapi.testclient import TestClient
        from main import app
        with tempfile.TemporaryDirectory() as tmp, patch.object(projects, 'PROJECTS_PATH', Path(tmp) / 'projects.json'), patch.dict(os.environ, {'SAB_ALLOW_PUBLIC_REGISTRATION': 'true'}), TestClient(app) as client:
            accounts = []
            for _ in range(2):
                suffix = uuid.uuid4().hex
                r = client.post('/api/auth/register', json={'account_name': suffix, 'email': suffix+'@example.test', 'password': 'project-test-password', 'name': 'project test'})
                self.assertEqual(201, r.status_code, r.text)
                accounts.append(r.json())
            h = [{'Authorization': 'Bearer '+a['access_token']} for a in accounts]
            # Pin this test's first account as operator without touching other accounts.
            import jwt
            owner = jwt.decode(accounts[0]['access_token'], options={'verify_signature': False})['account_id']
            with patch.dict(os.environ, {'SAB_OPERATOR_ACCOUNT_ID': owner}):
                p = client.post('/api/projects', headers=h[0], json={'name':'示例项目', 'app_ids':{'android':'com.example.app','harmony':'com.example.harmony'}, 'kb_path':tmp})
                self.assertEqual(201,p.status_code,p.text)
                pid = p.json()['id']
                r = client.post('/api/suites/manual', headers=h[0], json={'name':'平台测试','project_id':pid})
                self.assertEqual(201,r.status_code,r.text)
                sid=r.json()['id']
                self.assertEqual(pid, client.get('/api/suites/'+sid,headers=h[0]).json()['project_id'])
                self.assertEqual(400,client.patch('/api/suites/'+sid,headers=h[0],json={'project_id':'missing'}).status_code)
                self.assertEqual([],client.get('/api/projects',headers=h[1]).json())
                r=client.post('/api/suites/manual',headers=h[1],json={'name':'other','project_id':pid})
                self.assertEqual(403,r.status_code,r.text)
                r=client.patch('/api/suites/'+sid,headers=h[0],json={'project_id':''})
                self.assertEqual('',r.json()['project_id'])
