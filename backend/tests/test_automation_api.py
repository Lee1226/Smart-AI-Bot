"""Public catalog API: real ZIP data, authentication and tenant separation."""
import io
import json
import os
import uuid
import zipfile
from unittest.mock import patch

from fastapi.testclient import TestClient
from main import app


def test_public_catalog_upload_download_and_tenant_scope():
    with TestClient(app) as client, patch.dict(os.environ, {'SAB_ALLOW_PUBLIC_REGISTRATION': 'true'}):
        headers = []
        for _ in range(2):
            suffix = uuid.uuid4().hex
            response = client.post('/api/auth/register', json={
                'account_name': suffix, 'name': 'Catalog fixture',
                'email': suffix + '@example.test', 'password': 'fixture-password-only',
            })
            assert response.status_code == 201, response.text
            headers.append({'Authorization': 'Bearer ' + response.json()['access_token']})
        assert client.get('/api/automation/cases').status_code == 401
        flow = b'appId: com.example.fixture\n---\n- launchApp\n- assertVisible: Welcome\n'
        created = client.post('/api/automation/cases/workspace', headers=headers[0],
                              data={'case_id': 'public-fixture', 'name': 'Public fixture', 'flow_path': 'flow.yaml'},
                              files=[('files', ('flow.yaml', flow))])
        assert created.status_code == 201, created.text
        detail = client.get('/api/automation/cases/public-fixture', headers=headers[0])
        assert detail.status_code == 200
        assert 'Welcome' in detail.json()['versions'][0]['flow_yaml']
        assert client.get('/api/automation/cases/public-fixture', headers=headers[1]).status_code == 404
        assert client.get('/api/automation/cases', headers=headers[1]).json() == {'items': []}
        download = client.get('/api/automation/cases/public-fixture/versions/1/download', headers=headers[0])
        assert download.status_code == 200
        assert '.sabcase' in download.headers['content-disposition']
        with zipfile.ZipFile(io.BytesIO(download.content)) as package:
            assert package.read('flow.yaml') == flow
            assert json.loads(package.read('manifest.json'))['caseID'] == 'public-fixture'
        traversal = client.post('/api/automation/cases/workspace', headers=headers[0],
                                data={'case_id': 'bad-fixture', 'name': 'Bad', 'flow_path': '../flow.yaml'},
                                files=[('files', ('../flow.yaml', flow))])
        assert traversal.status_code == 400
