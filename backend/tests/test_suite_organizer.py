import io
import json
import unittest
import uuid
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from core.suite_organizer import ImportDraft, validate_coverage
from main import app


def model_output():
    return {"cases": [{"title": "打开设置页并检查展示", "preconditions": ["已登录"], "steps": [
        {"action": "点击设置入口", "assertions": [
            {"text": "标题为设置", "source_ids": ["source-1"]},
            {"text": "包含账号管理", "source_ids": ["source-2"]},
            {"text": "加载时间小于1秒", "source_ids": ["source-3"]},
        ]}]}], "changes": ["合并重复打开页面操作"], "review_notes": []}


def xmind_bytes():
    leaves = ["标题为设置", "包含账号管理", "加载时间小于1秒"]
    data = [{"rootTopic": {"title": "设置模块", "children": {"attached": [
        {"title": "打开设置页面", "children": {"attached": [{"title": item} for item in leaves]}}
    ]}}}]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        z.writestr("content.json", json.dumps(data))
    return output.getvalue()


class CoverageTests(unittest.TestCase):
    def draft(self):
        return ImportDraft(**model_output(), name="设置", sources=[
            {"id": f"source-{i}", "path": "设置", "expected": text}
            for i, text in enumerate(["标题为设置", "包含账号管理", "加载时间小于1秒"], 1)])

    def test_preserves_all_source_assertions_after_merging_steps(self):
        validate_coverage(self.draft())

    def test_rejects_omitted_or_fabricated_sources(self):
        draft = self.draft()
        draft.cases[0].steps[0].assertions.pop()
        with self.assertRaisesRegex(ValueError, "遗漏"):
            validate_coverage(draft)
        draft.cases[0].steps[0].assertions[0].source_ids = ["unknown"]
        with self.assertRaisesRegex(ValueError, "不存在"):
            validate_coverage(draft)


class OrganizerApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = TestClient(app)
        cls.client = cls.context.__enter__()
        suffix = uuid.uuid4().hex[:8]
        cls.headers = []
        for i in range(2):
            response = cls.client.post('/api/auth/register', json={"account_name": f"organizer-{suffix}-{i}",
                "email": f"organizer-{suffix}-{i}@example.test", "password": "test-only-password", "name": "Organizer test"})
            assert response.status_code == 201, response.text
            cls.headers.append({"Authorization": "Bearer " + response.json()["access_token"]})
        cls.client.put('/api/settings', json={"ark_api_key": "fake-test-key", "default_provider": "ark", "default_model": "test-model"}, headers=cls.headers[0])

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def preview(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(model_output())))])
        with patch('core.suite_organizer.resilient_completion', AsyncMock(return_value=response)):
            return self.client.post('/api/suites/import/ai-preview', files={"file": ("设置.xmind", xmind_bytes())}, data={"platform": "android"}, headers=self.headers[0])

    def test_preview_edit_confirm_and_original_mapping(self):
        before = self.client.get('/api/suites', headers=self.headers[0]).json()['total']
        result = self.preview()
        self.assertEqual(result.status_code, 200, result.text)
        draft = result.json()
        self.assertEqual(len(draft['sources']), 3)
        self.assertEqual(len(draft['cases']), 1)
        self.assertEqual(self.client.get('/api/suites', headers=self.headers[0]).json()['total'], before)
        self.assertTrue(any('计时' in note for note in draft['review_notes']))
        draft['app_package'] = 'com.example.app'
        draft['cases'][0]['steps'][0]['assertions'][0]['text'] = '页面标题显示“设置”'
        saved = self.client.post('/api/suites/import/ai-confirm', json=draft, headers=self.headers[0])
        self.assertEqual(saved.status_code, 201, saved.text)
        suite_id = saved.json()['id']
        nodes = self.client.get(f'/api/suites/{suite_id}/nodes', headers=self.headers[0]).json()
        self.assertEqual(len(nodes), 2)
        self.assertIn('页面标题显示', nodes[-1]['expected'])
        self.assertIn('加载时间小于1秒', nodes[-1]['expected'])
        cases = self.client.get(f'/api/suites/{suite_id}/cases', headers=self.headers[0]).json()
        metadata = json.loads(cases[0]['parameters'])
        self.assertEqual(len(metadata['source_cases']), 3)
        self.assertEqual(saved.json()['app_package'], 'com.example.app')
        foreign = self.client.post(f'/api/suites/{suite_id}/organize/preview', json={"platform": "android"}, headers=self.headers[1])
        self.assertEqual(foreign.status_code, 404)

    def test_invalid_coverage_does_not_create_suite(self):
        draft = self.preview().json()
        draft['cases'][0]['steps'][0]['assertions'].pop()
        before = self.client.get('/api/suites', headers=self.headers[0]).json()['total']
        response = self.client.post('/api/suites/import/ai-confirm', json=draft, headers=self.headers[0])
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get('/api/suites', headers=self.headers[0]).json()['total'], before)

    def test_missing_model_key_has_actionable_error(self):
        response = self.client.post('/api/suites/import/ai-preview', files={"file": ("设置.xmind", xmind_bytes())}, headers=self.headers[1])
        self.assertEqual(response.status_code, 422)
        self.assertIn('未配置', response.json()['detail'])
