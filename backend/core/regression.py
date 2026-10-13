"""Preparation and evidence for scheduled regression runs."""
import json
import secrets
import time
from pathlib import Path
from sqlalchemy import select, update
from db.database import AsyncSessionLocal, DB_PATH
from db.models import TestRun
from db.regression_models import RegressionPlan, RegressionTarget, RegressionArtifact
from agent.ws_device import WebSocketDevice
from ws.portal_ws import connected_devices

ARTIFACT_DIR = DB_PATH.parent / 'regression-artifacts'
grants = {}


async def context(run_id):
    async with AsyncSessionLocal() as db:
        target = (await db.execute(select(RegressionTarget).where(RegressionTarget.run_id == run_id))).scalar_one_or_none()
        if not target:
            return None
        return target, await db.get(RegressionPlan, target.plan_id), await db.get(TestRun, run_id)


async def prepare_run(run_id, emit):
    ctx = await context(run_id)
    if not ctx:
        return
    target, plan, run = ctx
    config = json.loads(plan.config_json)
    from core.test_runner import node_targets_for_suite
    async with AsyncSessionLocal() as db:
        targets = await node_targets_for_suite(db, run.suite_id)
    current = [{'node_id': t.node_id, 'steps': [{'action': c.action, 'expected': c.expected} for c in t.chain]} for t in targets]
    if current != config['suite_snapshot'].get(run.suite_id):
        raise RuntimeError('套件在排队后发生变更，请创建新计划；本次未执行')
    async with AsyncSessionLocal() as db:
        await db.execute(update(TestRun).where(TestRun.id == run_id).values(status='preparing'))
        await db.commit()
    if config.get('suite_apps'):
        from core.test_runner import _suite_app_package
        platform = connected_devices[run.device_id].platform
        package = await _suite_app_package(run.suite_id, platform)
        if package != config['suite_apps'].get(run.suite_id, {}).get(platform):
            raise RuntimeError('套件的应用配置发生变更，请创建新计划')
        if config.get('app_package') and package and config['app_package'] != package:
            raise RuntimeError('准备应用与被测应用不一致')
    device = WebSocketDevice(run.device_id)
    prep = {'environment': config['environment'], 'artifact_sha256': config.get('artifact_sha256', ''),
            'app_package': config.get('app_package', ''), 'clear_data': config.get('clear_data', False)}
    try:
        if config.get('artifact_id'):
            grant = secrets.token_urlsafe(32)
            grants[grant] = (config['artifact_id'], time.monotonic() + 900)
            try:
                prep['installed'] = await device._rpc('app/install', {
                    'download_url': config['download_base'] + '/api/regression-download/' + grant,
                    'sha256': config['artifact_sha256'], 'package': config['app_package']}, timeout=180)
            finally:
                grants.pop(grant, None)
            await emit('安装包已验证并安装：' + config['artifact_sha256'][:12])
        if config.get('clear_data'):
            prep['reset'] = await device._rpc('app/reset', {'package': config['app_package']})
            await emit('已清除被测应用数据')
        if config.get('app_package'):
            result = await device.start_app(config['app_package'])
            if result.startswith('ERROR:'):
                raise RuntimeError(result)
            prep['launch'] = result
        prep['status'] = 'ready'
    except BaseException:
        prep['status'] = 'failed'
        raise
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(update(RegressionTarget).where(RegressionTarget.id == target.id).values(preparation_json=json.dumps(prep, ensure_ascii=False)))
            await db.commit()


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
