import asyncio
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import projects
from core.test_accounts import LoginContext, encrypt, decrypt
from core.test_agent import TestCaseAgent, CaseResult, StepLog


class TestAccountSecretTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_input_never_returns_secret_and_results_redacted(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'SAB_TEST_ACCOUNT_KEY_PATH': tmp+'/.key'}):
            row=SimpleNamespace(name='普通用户', environment='测试服', notes='', login_mode='password',
                username_enc=encrypt('test-user-778899'), password_enc=encrypt('secret-pass-9988'))
            context=LoginContext(row)
            self.assertNotIn('secret-pass-9988', context.prompt())
            self.assertEqual('secret-pass-9988',decrypt(row.password_enc))
            self.assertNotIn('secret-pass-9988',row.password_enc)
            device=SimpleNamespace(platform='android',keyboard_visible=True,input_text=AsyncMock(return_value='Typed: secret-pass-9988'))
            agent=TestCaseAgent(device,'openai','test',login_context=context)
            result=await agent._dispatch('input_test_credential', {'field':'password'})
            device.input_text.assert_awaited_once_with('secret-pass-9988',False)
            self.assertNotIn('secret-pass-9988',result)
            self.assertNotIn('secret-pass-9988',json.dumps(agent._build_llm_kwargs([{'content':'secret-pass-9988'}])))
            agent._run=AsyncMock(return_value=CaseResult('skip','test-user-778899',1,log='secret-pass-9988',action_history=[{'text':'secret-pass-9988'}]))
            result=await agent.run(None)
            self.assertNotIn('secret-pass-9988',json.dumps(result.__dict__))
            self.assertNotIn('test-user-778899',result.reason)
            device.input_text.side_effect=ValueError('cannot type secret-pass-9988')
            result=await agent._dispatch('input_test_credential', {'field':'password'})
            self.assertNotIn('secret-pass-9988',result)
            device.input_text.side_effect=None
            context.login_mode='email'
            self.assertIn('email and password',context.prompt())
            self.assertIn('Filled',await agent._dispatch('input_test_credential', {'field':'password'}))
            context.login_mode='phone'
            self.assertIn('not password',await agent._dispatch('input_test_credential', {'field':'password'}))
            context.verification_code='483921'
            self.assertNotIn('483921', context.prompt())
            self.assertIn('Filled', await agent._dispatch('input_test_credential', {'field':'verification_code'}))
            device.input_text.assert_awaited_with('483921', False)
            self.assertNotIn('483921', context.redact('code=483921'))
            context.login_mode='email'
            self.assertIn('does not use', await agent._dispatch('input_test_credential', {'field':'verification_code'}))
            device.keyboard_visible=False
            self.assertIn('Focus',await agent._dispatch('input_test_credential', {'field':'password'}))
            self.assertEqual(0o600,Path(tmp+'/.key').stat().st_mode & 0o777)


class TestAccountApiTests(unittest.TestCase):
    def test_crud_default_tenant_isolation_and_leases(self):
        from fastapi.testclient import TestClient
        from main import app
        import jwt
        from db.database import AsyncSessionLocal
        from db.models import TestRun, ProjectTestAccount, TestSuite
        from core.test_accounts import select_run_account, release_run_account
        with tempfile.TemporaryDirectory() as tmp, patch.object(projects,'PROJECTS_PATH',Path(tmp)/'projects.json'), patch.dict(os.environ, {'SAB_ALLOW_PUBLIC_REGISTRATION':'true','SAB_TEST_ACCOUNT_KEY_PATH':tmp+'/.key'}), TestClient(app) as client:
            headers=[];owners=[]
            for _ in range(2):
                u=uuid.uuid4().hex
                r=client.post('/api/auth/register',json={'account_name':u,'email':u+'@example.test','password':'test-account-fixture','name':'fixture'})
                self.assertEqual(201,r.status_code,r.text)
                token=r.json()['access_token'];headers.append({'Authorization':'Bearer '+token});owners.append(jwt.decode(token,options={'verify_signature':False})['account_id'])
            with patch.dict(os.environ,{'SAB_OPERATOR_ACCOUNT_ID':owners[0]}):
                p=client.post('/api/projects',headers=headers[0],json={'name':'fixture','app_ids':{'android':'com.example.test'}}).json()
                path='/api/projects/'+p['id']+'/test-accounts'
                body={'name':'普通用户','username':'login-user-7788','password':'fixture-pass-9988','login_mode':'password','is_default':True,'platforms':['android'],'notes':'密码fixture-pass-9988不要输出'}
                r=client.post(path,headers=headers[0],json=body);self.assertEqual(201,r.status_code,r.text)
                row=r.json();rid=row['id']
                self.assertNotIn(body['password'],r.text);self.assertNotIn(body['username'],r.text)
                self.assertEqual(True,row['password_configured'])
                self.assertEqual(403,client.get(path,headers=headers[1]).status_code)
                r=client.post(path,headers=headers[0],json={**body,'password':'x'*501})
                self.assertEqual(422,r.status_code);self.assertNotIn('x'*501,r.text)
                r=client.put(path+'/'+rid,headers=headers[0],json={'name':'修改名称','platforms':['android'],'login_mode':'password','is_default':True})
                self.assertEqual(200,r.status_code,r.text);self.assertEqual(True,r.json()['password_configured'])
                for mode, username in [('phone', '+8613800000000'), ('email', 'fixture@example.test')]:
                    r=client.post(path,headers=headers[0],json={'name':mode,'username':username,'login_mode':mode, **({'password':'email-fixture-password'} if mode == 'email' else {'verification_code':'483921'})})
                    self.assertEqual(201,r.status_code,r.text)
                    self.assertEqual(mode,r.json()['login_mode'])
                    self.assertEqual(mode == 'email',r.json()['password_configured'])
                    self.assertNotIn(username,r.text)
                    self.assertNotIn('483921',r.text)
                    self.assertEqual(mode == 'phone', r.json()['verification_code_configured'])
                    if mode == 'phone':
                        phone_id=r.json()['id']
                        edited=client.put(path+'/'+phone_id, headers=headers[0], json={'name':'phone', 'login_mode':'phone'})
                        self.assertEqual(200, edited.status_code, edited.text)
                        self.assertTrue(edited.json()['verification_code_configured'])
                        cleared=client.put(path+'/'+phone_id, headers=headers[0], json={'name':'phone', 'login_mode':'phone', 'verification_code':''})
                        self.assertFalse(cleared.json()['verification_code_configured'])
                r=client.post(path,headers=headers[0],json={'name':'legacy','username':'fixture','login_mode':'manual'})
                self.assertEqual(422,r.status_code)
                r=client.post(path,headers=headers[0],json={'name':'no email password','username':'fixture@example.test','login_mode':'email'})
                self.assertEqual(422,r.status_code,r.text)
                r=client.post(path,headers=headers[0],json={'name':'bad email','username':'not-an-email','login_mode':'email'})
                self.assertEqual(422,r.status_code,r.text)
                suite=client.post('/api/suites/manual',headers=headers[0],json={'name':'fixture','project_id':p['id']}).json()
                async def reserve():
                    async with AsyncSessionLocal() as db:
                        a=await db.get(ProjectTestAccount,rid)
                        self.assertNotIn(body['password'],a.password_enc);self.assertEqual(body['password'],decrypt(a.password_enc))
                        run=TestRun(suite_id=suite['id'],device_id='fixture-device',account_id=owners[0],status='pending')
                        db.add(run);await db.flush()
                        await select_run_account(db,run,'','android');await db.commit()
                        self.assertEqual(rid,run.test_account_id)
                        return run.id
                run_id=client.portal.call(reserve)
                self.assertEqual(409,client.put(path+'/'+rid,headers=headers[0],json={'name':'busy'}).status_code)
                async def unavailable():
                    from fastapi import HTTPException
                    async with AsyncSessionLocal() as db:
                        r=TestRun(suite_id=suite['id'],device_id='other',account_id=owners[0],status='pending');db.add(r);await db.flush()
                        with self.assertRaises(HTTPException) as err:
                            await select_run_account(db,r,rid,'android')
                        self.assertEqual(409,err.exception.status_code)
                        await db.rollback()
                    await release_run_account(run_id)
                client.portal.call(unavailable)
                # Exercise the actual run endpoint without driving a real device/LLM.
                from ws.portal_ws import connected_devices
                from core.test_accounts import context_for_run
                async def add_device():
                    from db.models import Device
                    async with AsyncSessionLocal() as db:
                        device=Device(name='fixture', token=uuid.uuid4().hex, account_id=owners[0])
                        db.add(device);await db.commit()
                        return device.id
                did=client.portal.call(add_device)
                node=client.post('/api/suites/'+suite['id']+'/nodes',headers=headers[0],json={'action':'检查登录前置条件'}).json()
                with patch.dict(connected_devices,{did:SimpleNamespace(is_connected=True,platform='android')}), patch('core.test_runner.start_tree_run',new=AsyncMock()):
                    r=client.post('/api/runs/tree',headers=headers[0],json={'suite_id':suite['id'],'device_id':did,'test_account_id':rid})
                    self.assertEqual(201,r.status_code,r.text)
                    self.assertEqual('修改名称',r.json()['test_account_name'])
                    self.assertNotIn(body['password'],r.text)
                    attached_run=r.json()['id']
                    async def verify_context():
                        ctx=await context_for_run(attached_run)
                        self.assertEqual(body['username'],ctx.username)
                        self.assertEqual(body['password'],ctx.password)
                        from core.test_runner import RunState
                        state=RunState(attached_run)
                        state.redact=ctx.redact
                        await state.emit(body['password'])
                        self.assertNotIn(body['password'],state.logs[0])
                        # The same finish hook runs when a queued task is cancelled.
                        await state.finish()
                    client.portal.call(verify_context)
                # Regression creation used to bypass account selection entirely.
                with patch.dict(connected_devices,{did:SimpleNamespace(is_connected=True,platform='android')}), patch('routers.regressions.start_tree_run',new=AsyncMock()):
                    payload={'name':'account regression','suite_ids':[suite['id']],'device_ids':[did]}
                    r=client.post('/api/regressions',headers=headers[0],json=payload)
                    self.assertEqual(201,r.status_code,r.text)
                    plan=r.json()
                    self.assertEqual(rid,plan['targets'][0]['test_account_id'])
                    self.assertEqual('修改名称',plan['targets'][0]['test_account_name'])
                    self.assertNotIn(body['password'],r.text)
                    regression_run=plan['targets'][0]['run_id']
                    async def finish_regression():
                        from sqlalchemy import update
                        from db.models import TestResult
                        async with AsyncSessionLocal() as db:
                            await db.execute(update(TestRun).where(TestRun.id==regression_run).values(status='done'))
                            await db.execute(update(TestResult).where(TestResult.run_id==regression_run).values(status='skip'))
                            await db.commit()
                        await release_run_account(regression_run)
                    client.portal.call(finish_regression)
                    r=client.post('/api/regressions/'+plan['id']+'/retry',headers=headers[0])
                    self.assertEqual(201,r.status_code,r.text)
                    self.assertEqual(rid,r.json()['targets'][0]['test_account_id'])
                    client.portal.call(release_run_account,r.json()['targets'][0]['run_id'])
                    r=client.post('/api/regressions',headers=headers[0],json={**payload,'test_accounts':{suite['id']+':'+did:'-'}})
                    self.assertEqual(201,r.status_code,r.text)
                    self.assertEqual('',r.json()['targets'][0]['test_account_id'])
                r=client.post(path,headers=headers[0],json={**body,'name':'另一个默认'})
                self.assertEqual(201,r.status_code,r.text)
                rows=client.get(path,headers=headers[0]).json();self.assertEqual(1,sum(a['is_default'] for a in rows))
                self.assertEqual(204,client.delete(path+'/'+rid,headers=headers[0]).status_code)
                self.assertNotIn(rid,[a['id'] for a in client.get(path,headers=headers[0]).json()])
