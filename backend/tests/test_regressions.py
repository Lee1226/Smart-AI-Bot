import asyncio
import io
import json
import unittest
import uuid
import zipfile
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy import update
from main import app
from db.database import AsyncSessionLocal
from db.models import TestRun, TestResult
from core import device_scheduler
from ws.portal_ws import connected_devices

async def set_results(plan, status='done',result='pass'):
    async with AsyncSessionLocal() as db:
        for target in plan['targets']:
            await db.execute(update(TestRun).where(TestRun.id==target['run_id']).values(status=status))
            await db.execute(update(TestResult).where(TestResult.run_id==target['run_id']).values(status=result))
        await db.commit()

class RegressionAPITests(unittest.TestCase):
    def test_accounts_matrix_fail_closed_gate_retry_and_archive(self):
        with patch.dict('os.environ',{'SAB_ALLOW_PUBLIC_REGISTRATION':'true'}),patch('routers.regressions.start_tree_run',new=AsyncMock()) as launch,TestClient(app) as client:
            headers=[]
            for _ in range(2):
                suffix=uuid.uuid4().hex
                r=client.post('/api/auth/register',json={'account_name':suffix,'email':suffix+'@test.example','password':'test-only-password','name':'Regression test'})
                self.assertEqual(201,r.status_code,r.text)
                headers.append({'Authorization':'Bearer '+r.json()['access_token']})
            suites=[]; devices=[]
            for i in range(2):
                r=client.post('/api/suites/manual',headers=headers[0],json={'name':'Regression '+str(i),'app_package':'com.test.app'})
                self.assertEqual(201,r.status_code,r.text);sid=r.json()['id'];suites.append(sid)
                self.assertEqual(201,client.post('/api/suites/'+sid+'/nodes',headers=headers[0],json={'action':'open settings','expected':'settings visible'}).status_code)
                r=client.post('/api/devices',headers=headers[0],json={'name':'Phone '+str(i),'connection_type':'adb_bridge'})
                self.assertEqual(201,r.status_code,r.text);did=r.json()['id'];devices.append(did)
                connected_devices[did]=SimpleNamespace(is_connected=True,connection_type='adb_bridge',platform='android')
            try:
                payload={'name':'Matrix','suite_ids':suites,'device_ids':devices}
                self.assertEqual(422,client.post('/api/regressions',headers=headers[0],json={**payload,'app_package':'com.wrong.app'}).status_code)
                r=client.post('/api/regressions',headers=headers[0],json=payload)
                self.assertEqual(201,r.status_code,r.text);plan=r.json();self.assertEqual(4,len(plan['targets']));self.assertEqual(4,launch.await_count)
                path='/api/regressions/'+plan['id']
                self.assertEqual('waiting',plan['gate'])
                self.assertFalse(client.get(path+'/gate',headers=headers[0]).json()['release_allowed'])
                self.assertEqual(409,client.get(path+'/evidence.zip',headers=headers[0]).status_code)
                self.assertIn(b'<error',client.get(path+'/junit.xml',headers=headers[0]).content)
                for suffix in ('','/gate','/junit.xml','/evidence.zip','/retry','/cancel'):
                    method=client.post if suffix in ('/retry','/cancel') else client.get
                    self.assertEqual(404,method(path+suffix,headers=headers[1]).status_code)
                asyncio.run(set_results(plan))
                self.assertTrue(client.get(path+'/gate',headers=headers[0]).json()['release_allowed'])
                device_scheduler.jobs[plan['targets'][0]['run_id']] = object()
                try:
                    self.assertFalse(client.get(path+'/gate',headers=headers[0]).json()['release_allowed'])
                    self.assertIn(b'evidence finalization',client.get(path+'/junit.xml',headers=headers[0]).content)
                finally:
                    device_scheduler.jobs.pop(plan['targets'][0]['run_id'])
                one={'targets':[plan['targets'][0]]};asyncio.run(set_results(one,result='skip'))
                self.assertFalse(client.get(path+'/gate',headers=headers[0]).json()['release_allowed'])
                self.assertIn(b'<error',client.get(path+'/junit.xml',headers=headers[0]).content)
                r=client.post(path+'/retry',headers=headers[0]);self.assertEqual(201,r.status_code,r.text);self.assertEqual(1,len(r.json()['targets']))
                with zipfile.ZipFile(io.BytesIO(client.get(path+'/evidence.zip',headers=headers[0]).content)) as archive:
                    self.assertEqual('failed',json.loads(archive.read('plan.json'))['gate'])
                    self.assertTrue(any(n.endswith('execution.log') for n in archive.namelist()))
                asyncio.run(set_results(plan,status='interrupted'))
                self.assertFalse(client.get(path+'/gate',headers=headers[0]).json()['release_allowed'])
                # Upload rejection, artifact isolation and unguessable expired grant.
                self.assertEqual(422,client.post('/api/regressions/artifacts',headers=headers[0],files={'file':('bad.apk',b'no zip')}).status_code)
                buf=io.BytesIO()
                with zipfile.ZipFile(buf,'w') as z:z.writestr('AndroidManifest.xml','test fixture')
                r=client.post('/api/regressions/artifacts',headers=headers[0],files={'file':('fixture.apk',buf.getvalue())});self.assertEqual(201,r.status_code,r.text)
                self.assertEqual([],client.get('/api/regressions/artifacts',headers=headers[1]).json())
                self.assertEqual(404,client.post('/api/regressions',headers=headers[1],json={**payload,'artifact_id':r.json()['id'],'app_package':'com.test.app'}).status_code)
                self.assertEqual(404,client.get('/api/regression-download/expired').status_code)
            finally:
                for did in devices:connected_devices.pop(did,None)
