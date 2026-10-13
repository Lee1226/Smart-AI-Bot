import asyncio
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy import update
from db.database import init_db, AsyncSessionLocal
from db.models import TestRun, TestResult
from db import regression_models
from core import device_scheduler as scheduler
from core.test_runner import RunState
from ws.portal_ws import connected_devices


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        self.prep = patch('core.regression.prepare_run', new=AsyncMock()); self.prep.start()
        self.diag = patch('core.regression.collect_diagnostics', new=AsyncMock()); self.diag.start()
        self.ids=[]

    async def asyncTearDown(self):
        await scheduler.shutdown()
        self.prep.stop(); self.diag.stop()
        for did in self.ids: connected_devices.pop(did, None)

    async def run_row(self, did):
        connected_devices[did]=SimpleNamespace(is_connected=True)
        self.ids.append(did)
        async with AsyncSessionLocal() as db:
            row=TestRun(suite_id='test-suite',device_id=did)
            db.add(row); await db.flush()
            db.add(TestResult(run_id=row.id,case_id='test-case',status='pending'))
            await db.commit()
            return row.id

    async def status(self,rid):
        async with AsyncSessionLocal() as db: return (await db.get(TestRun,rid)).status

    async def complete(self,rid):
        async with AsyncSessionLocal() as db:
            await db.execute(update(TestRun).where(TestRun.id==rid).values(status='done'))
            await db.commit()

    async def test_same_device_fifo_other_device_overlap_and_waiter_cancel(self):
        d1,d2=uuid.uuid4().hex,uuid.uuid4().hex
        a,b,c=await self.run_row(d1),await self.run_row(d1),await self.run_row(d2)
        entered_a,entered_c,release=asyncio.Event(),asyncio.Event(),asyncio.Event()
        calls=[]
        async def execute(rid,event):
            calls.append(rid);event.set();await release.wait();await self.complete(rid)
        await scheduler.enqueue(a,RunState(),lambda:execute(a,entered_a))
        await asyncio.wait_for(entered_a.wait(),2)
        await scheduler.enqueue(b,RunState(),lambda:execute(b,asyncio.Event()))
        await scheduler.enqueue(c,RunState(),lambda:execute(c,entered_c))
        await asyncio.wait_for(entered_c.wait(),2)
        self.assertEqual('queued',await self.status(b));self.assertNotIn(b,calls)
        await scheduler.cancel(b)
        self.assertEqual('cancelled',await self.status(b));self.assertEqual(a,scheduler.owners[d1])
        tasks=list(scheduler.jobs.values());release.set();await asyncio.gather(*tasks)
        self.assertEqual({a,c},set(calls));self.assertFalse(scheduler.owners)
        # A later queued task proceeds only after the earlier task releases.
        release.clear();entered_a.clear()
        x,y=await self.run_row(d1),await self.run_row(d1)
        await scheduler.enqueue(x,RunState(),lambda:execute(x,entered_a))
        await asyncio.wait_for(entered_a.wait(),2)
        await scheduler.enqueue(y,RunState(),lambda:execute(y,asyncio.Event()))
        tasks=list(scheduler.jobs.values());release.set();await asyncio.gather(*tasks)
        self.assertEqual([x,y],calls[-2:]);self.assertFalse(scheduler.locks[d1].locked())

    async def test_cancel_before_start_exception_offline_and_restart(self):
        did=uuid.uuid4().hex
        rid=await self.run_row(did);executor=AsyncMock()
        await scheduler.enqueue(rid,RunState(),executor);await scheduler.cancel(rid)
        self.assertEqual('cancelled',await self.status(rid));executor.assert_not_awaited()
        rid=await self.run_row(did)
        await scheduler.enqueue(rid,RunState(),AsyncMock(side_effect=RuntimeError('intentional test error')))
        await scheduler.jobs[rid]
        self.assertEqual('error',await self.status(rid));self.assertFalse(scheduler.locks[did].locked())
        connected_devices.pop(did)
        rid=await self.run_row(did);connected_devices.pop(did)
        await scheduler.enqueue(rid,RunState(),executor);await scheduler.jobs[rid]
        self.assertEqual('error',await self.status(rid))
        pending=await self.run_row(did)
        await scheduler.recover_interrupted_runs()
        self.assertEqual('interrupted',await self.status(pending))

    async def test_cancel_running_and_double_cancel_during_diagnostics_releases_lock(self):
        did=uuid.uuid4().hex;rid=await self.run_row(did)
        started,diagnosing=asyncio.Event(),asyncio.Event()
        async def execute():started.set();await asyncio.Event().wait()
        async def diagnostics(*args):diagnosing.set();await asyncio.Event().wait()
        with patch('core.regression.collect_diagnostics',side_effect=diagnostics):
            await scheduler.enqueue(rid,RunState(),execute)
            await asyncio.wait_for(started.wait(),2)
            task=scheduler.jobs[rid];task.cancel()
            await asyncio.wait_for(diagnosing.wait(),2);task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertEqual('cancelled',await self.status(rid));self.assertFalse(scheduler.jobs)
        self.assertFalse(scheduler.owners);self.assertFalse(scheduler.locks[did].locked())
