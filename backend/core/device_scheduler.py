"""Whole-run device ownership for the single backend worker.

SQLite run rows survive restarts; interrupted work is never silently resumed.
The device WebSocket registry is process-local, so this scheduler intentionally
shares that deployment boundary (one backend worker).
"""
import asyncio
import logging
from datetime import datetime

from sqlalchemy import select, update
from db.database import AsyncSessionLocal
from db.models import TestRun, TestResult

log = logging.getLogger(__name__)
locks = {}
owners = {}
jobs = {}


async def settle(run_id, status, reason):
    async with AsyncSessionLocal() as db:
        await db.execute(update(TestRun).where(TestRun.id == run_id).values(
            status=status, finished_at=datetime.utcnow()))
        await db.execute(update(TestResult).where(TestResult.run_id == run_id,
            TestResult.status.in_(['pending', 'running'])).values(
                status='skip' if status == 'cancelled' else 'error', reason=reason,
                finished_at=datetime.utcnow()))
        await db.commit()


async def recover_interrupted_runs():
    async with AsyncSessionLocal() as db:
        ids = (await db.execute(select(TestRun.id).where(
            TestRun.status.in_(['pending', 'queued', 'preparing', 'running'])))).scalars().all()
    for run_id in ids:
        await settle(run_id, 'interrupted', '服务重启，任务中断；请确认设备状态后重新运行')
    return len(ids)


async def shutdown():
    tasks = list(jobs.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    locks.clear(); owners.clear(); jobs.clear()


async def enqueue(run_id, state, executor):
    from ws.portal_ws import connected_devices
    from core.test_runner import active_runs
    if run_id in jobs:
        raise ValueError('Run already scheduled')
    async with AsyncSessionLocal() as db:
        run = await db.get(TestRun, run_id)
        if not run:
            raise ValueError('Run not found')
        device_id = run.device_id
        await db.execute(update(TestRun).where(TestRun.id == run_id).values(status='queued'))
        await db.commit()
    lock = locks.setdefault(device_id, asyncio.Lock())
    active_runs[run_id] = state
    await state.emit('等待设备空闲，任务已进入队列')

    async def work():
        acquired = False
        try:
            await lock.acquire()
            acquired = True
            owners[device_id] = run_id
            conn = connected_devices.get(device_id)
            if not conn or not conn.is_connected:
                raise RuntimeError('设备离线，未执行；请重新连接后重跑')
            await state.emit('已获得设备独占权，开始环境准备')
            from core.regression import prepare_run
            await prepare_run(run_id, state.emit)
            await executor()
            # Older executors may swallow cancellation or leave pending
            # results; finish their rows consistently before releasing.
            async with AsyncSessionLocal() as db:
                row = await db.get(TestRun, run_id)
                status = row.status if row else 'error'
            if status == 'cancelled':
                await settle(run_id, 'cancelled', '任务已取消')
            elif status not in ('done', 'error'):
                await settle(run_id, 'error', '执行器提前结束，未产生完整结果')
        except asyncio.CancelledError:
            await settle(run_id, 'cancelled', '任务已取消')
            await state.emit('任务已取消，正在收尾')
        except Exception as exc:
            log.exception('Run %s failed', run_id)
            await settle(run_id, 'error', str(exc))
            await state.emit('任务错误：' + str(exc))
        finally:
            if acquired:
                try:
                    from core.regression import collect_diagnostics
                    await asyncio.wait_for(collect_diagnostics(run_id, state.emit), timeout=35)
                except (Exception, asyncio.CancelledError):
                    log.exception('Diagnostic collection interrupted for %s', run_id)
                finally:
                    if owners.get(device_id) == run_id:
                        owners.pop(device_id, None)
                    lock.release()
            jobs.pop(run_id, None)
            active_runs.pop(run_id, None)
            await state.finish()
    task = asyncio.create_task(work())
    jobs[run_id] = task
    state.task = task


async def cancel(run_id):
    task = jobs.get(run_id)
    if task is None:
        return False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    # Covers cancellation before work() received its first event-loop turn.
    if jobs.get(run_id) is task:
        jobs.pop(run_id, None)
        from core.test_runner import active_runs
        state = active_runs.pop(run_id, None)
        await settle(run_id, 'cancelled', '任务已取消')
        if state:
            await state.finish()
    return True
