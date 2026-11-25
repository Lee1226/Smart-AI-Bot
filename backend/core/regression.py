"""Preparation and evidence for scheduled regression runs."""
import json
from sqlalchemy import select, update
from db.database import AsyncSessionLocal
from db.models import TestRun
from db.regression_models import RegressionPlan, RegressionTarget
from agent.ws_device import WebSocketDevice
from ws.portal_ws import connected_devices


async def context(run_id):
    async with AsyncSessionLocal() as db:
        target = (await db.execute(select(RegressionTarget).where(RegressionTarget.run_id == run_id))).scalar_one_or_none()
        if not target:
            return None
        return target, await db.get(RegressionPlan, target.plan_id), await db.get(TestRun, run_id)


async def prepare_run(run_id, emit):
    """Validate the queued suite snapshot and launch its configured app."""
    ctx = await context(run_id)
    if not ctx:
        return
    _, plan, run = ctx
    config = json.loads(plan.config_json)
    from core.test_runner import node_targets_for_suite, _suite_app_package
    async with AsyncSessionLocal() as db:
        targets = await node_targets_for_suite(db, run.suite_id)
    current = [{'node_id': t.node_id, 'steps': [{'action': c.action, 'expected': c.expected} for c in t.chain]} for t in targets]
    if current != config['suite_snapshot'].get(run.suite_id):
        raise RuntimeError('套件在排队后发生变更，请创建新计划；本次未执行')
    async with AsyncSessionLocal() as db:
        await db.execute(update(TestRun).where(TestRun.id == run_id).values(status='preparing'))
        await db.commit()
    device = WebSocketDevice(run.device_id)
    platform = device.platform
    expected_package = config.get('suite_apps', {}).get(run.suite_id, {}).get(platform, '')
    current_package = await _suite_app_package(run.suite_id, platform)
    if current_package != expected_package:
        raise RuntimeError('套件的应用配置发生变更，请创建新计划')
    if current_package:
        result = await device.start_app(current_package)
        if result.startswith('ERROR:'):
            raise RuntimeError(result)
        await emit('已启动被测应用：' + current_package)


async def collect_diagnostics(run_id, emit):
    ctx = await context(run_id)
    if not ctx:
        return
    target, _, run = ctx
    conn = connected_devices.get(run.device_id)
    if not conn or conn.connection_type != 'adb_bridge':
        data = {'status': 'unsupported', 'reason': '此连接方式仅归档步骤截图与执行日志'}
    else:
        try:
            data = await WebSocketDevice(run.device_id)._rpc('diagnostics', {}, timeout=30)
            await emit('已归档设备日志与诊断信息')
        except Exception as exc:
            data = {'status': 'error', 'reason': str(exc)}
            await emit('设备诊断采集失败，步骤证据仍可下载')
    async with AsyncSessionLocal() as db:
        await db.execute(update(RegressionTarget).where(RegressionTarget.id == target.id).values(diagnostics_json=json.dumps(data, ensure_ascii=False)))
        await db.commit()
