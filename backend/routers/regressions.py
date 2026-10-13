"""Account-scoped multi-device regression plans and CI artifacts."""
import asyncio
import base64
import hashlib
import tempfile
from pathlib import Path
from starlette.background import BackgroundTask
import json
import time
import uuid
import zipfile
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.access import require_device, require_suite
from core.regression import ARTIFACT_DIR, grants
from core.test_runner import node_targets_for_suite, start_tree_run, cancel_run
from db.database import AsyncSessionLocal
from db.models import TestRun, TestResult, TestStepLog
from db.regression_models import RegressionArtifact, RegressionPlan, RegressionTarget
from routers.testruns import _default_provider_model, _run_out, _validate_project_target
from ws.portal_ws import connected_devices

router = APIRouter(prefix='/api/regressions', tags=['regressions'])
download_router = APIRouter()
TERMINAL = {'done', 'error', 'cancelled', 'interrupted'}


async def get_db():
    async with AsyncSessionLocal() as db:
        yield db


async def owned(db, model, resource_id, request):
    row = await db.get(model, resource_id)
    if not row or row.account_id != request.state.account_id:
        raise HTTPException(404, 'Not found')
    return row


class PlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    suite_ids: list[str] = Field(min_length=1, max_length=20)
    device_ids: list[str] = Field(min_length=1, max_length=20)
    environment: str = Field(default='默认环境', max_length=120)
    test_accounts: dict[str, str] = Field(default_factory=dict)  # suite_id:device_id -> account ID, -=none
    artifact_id: str = ''
    app_package: str = ''
    clear_data: bool = False
    provider: str = ''
    model: str = ''
    max_steps: int = Field(default=20, ge=1, le=100)
    commit_sha: str = Field(default='', max_length=120)
    build_url: str = Field(default='', max_length=1000)


async def detail(plan, db, summary=False):
    targets = (await db.execute(select(RegressionTarget).where(RegressionTarget.plan_id == plan.id))).scalars().all()
    rows = []
    for target in targets:
        run = await db.get(TestRun, target.run_id)
        if not run:
            rows.append({'run_id': target.run_id, 'suite_name': target.suite_name, 'device_name': target.device_name,
                         'status': 'missing', 'passed': 0, 'failed': 0, 'errored': 1, 'skipped': 0, 'total': 0})
            continue
        out = (await _run_out(run, db)).model_dump()
        problems = (await db.execute(select(TestResult).where(TestResult.run_id == run.id,
            TestResult.status.in_(['fail', 'error', 'skip'])))).scalars().all()
        out['problems'] = [{'status': r.status, 'reason': r.reason} for r in problems]
        out.update(run_id=run.id, suite_name=target.suite_name, device_name=target.device_name,
                   preparation=json.loads(target.preparation_json), diagnostics=json.loads(target.diagnostics_json))
        if summary:
            out['diagnostics'] = {k: v for k, v in out['diagnostics'].items() if k not in {'logcat', 'last_anr'}}
        rows.append(out)
    from core.device_scheduler import jobs
    active = any(r['run_id'] in jobs for r in rows) or any(r['status'] not in TERMINAL and r['status'] != 'missing' for r in rows)
    passed = bool(rows) and not active and all(r['status'] == 'done' and r['total'] > 0 and
        r['passed'] == r['total'] and not (r['failed'] or r['errored'] or r['skipped']) for r in rows)
    return {'id': plan.id, 'name': plan.name, 'created_at': plan.created_at.isoformat(),
            'config': json.loads(plan.config_json), 'targets': rows,
            'gate': 'waiting' if active else 'passed' if passed else 'failed'}


@router.post('/artifacts', status_code=201)
async def upload_artifact(request: Request, file: UploadFile = File(...), version_label: str = Form(''), db: AsyncSession = Depends(get_db)):
    if not (file.filename or '').lower().endswith('.apk'):
        raise HTTPException(422, '当前仅支持 APK 安装包')
    if len(version_label) > 120:
        raise HTTPException(422, '版本标签过长')
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    artifact_id = uuid.uuid4().hex
    path = ARTIFACT_DIR / (artifact_id + '.apk')
    digest, size = hashlib.sha256(), 0
    try:
        with path.open('wb') as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise HTTPException(413, 'APK 大于 512 MB')
                digest.update(chunk); output.write(chunk)
        try:
            with zipfile.ZipFile(path) as apk:
                if 'AndroidManifest.xml' not in apk.namelist():
                    raise ValueError()
        except (ValueError, zipfile.BadZipFile):
            raise HTTPException(422, '无效 APK 文件')
        artifact = RegressionArtifact(id=artifact_id, account_id=request.state.account_id,
            filename=(file.filename or '').replace('\\', '/').split('/')[-1],
            version_label=version_label.strip(), sha256=digest.hexdigest())
        db.add(artifact); await db.commit()
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {'id': artifact.id, 'filename': artifact.filename, 'version_label': artifact.version_label, 'sha256': artifact.sha256}


@router.get('/artifacts')
async def list_artifacts(request: Request, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(RegressionArtifact).where(RegressionArtifact.account_id == request.state.account_id).order_by(RegressionArtifact.created_at.desc()))).scalars().all()
    return [{'id': r.id, 'filename': r.filename, 'version_label': r.version_label, 'sha256': r.sha256} for r in rows]


@download_router.get('/api/regression-download/{grant}')
async def download_artifact(grant: str):
    item = grants.get(grant)
    if not item or item[1] < time.monotonic():
        grants.pop(grant, None)
        raise HTTPException(404, 'Download grant expired')
    return FileResponse(ARTIFACT_DIR / (item[0] + '.apk'), media_type='application/vnd.android.package-archive')


@router.post('', status_code=201)
async def create_plan(body: PlanIn, request: Request, db: AsyncSession = Depends(get_db)):
    if not body.name.strip() or len(body.suite_ids) != len(set(body.suite_ids)) or len(body.device_ids) != len(set(body.device_ids)):
        raise HTTPException(422, '请填写名称，设备和套件不能重复')
    if len(body.suite_ids) * len(body.device_ids) > 20:
        raise HTTPException(422, '一次计划最多 20 个设备与套件组合')
    if (body.artifact_id or body.clear_data) and not body.app_package.strip():
        raise HTTPException(422, '安装或清数据必须指定被测应用包名')
    artifact = await owned(db, RegressionArtifact, body.artifact_id, request) if body.artifact_id else None
    devices, suites, versions, targets_by_suite, suite_apps = [], [], {}, {}, {}
    for sid in body.suite_ids:
        suite = await require_suite(request, db, sid)
        targets = await node_targets_for_suite(db, sid)
        if not targets:
            raise HTTPException(422, '套件没有可运行步骤：' + suite.name)
        suites.append(suite); targets_by_suite[sid] = targets
        versions[sid] = [{'node_id': t.node_id, 'steps': [{'action': c.action, 'expected': c.expected} for c in t.chain]} for t in targets]
    for did in body.device_ids:
        device = await require_device(request, db, did)
        conn = connected_devices.get(did)
        if not conn or not conn.is_connected:
            raise HTTPException(422, '设备离线：' + device.name)
        if (artifact or body.clear_data) and device.connection_type != 'adb_bridge':
            raise HTTPException(422, '自动安装和清数据当前仅支持 Android ADB 桥接：' + device.name)
        for sid in body.suite_ids:
            await _validate_project_target(db, sid, did)
            from core.test_runner import _suite_app_package
            package = await _suite_app_package(sid, conn.platform)
            suite_apps.setdefault(sid, {})[conn.platform] = package
            if body.app_package.strip() and package and body.app_package.strip() != package:
                raise HTTPException(422, '准备应用包名与套件被测应用不一致，请为被测应用选择对应套件')
        devices.append(device)
    default_provider, default_model = await _default_provider_model(request.state.account_id)
    config = body.model_dump()
    config.update(name=body.name.strip(), provider=body.provider or default_provider,
                  model=body.model or default_model, app_package=body.app_package.strip(),
                  artifact_sha256=artifact.sha256 if artifact else '', artifact_filename=artifact.filename if artifact else '',
                  artifact_version_label=artifact.version_label if artifact else '',
                  download_base=str(request.base_url).rstrip('/'), suite_snapshot=versions, suite_apps=suite_apps)
    plan = RegressionPlan(account_id=request.state.account_id, name=body.name.strip(), config_json=json.dumps(config, ensure_ascii=False))
    db.add(plan); await db.flush()
    runs = []
    for device in devices:
        for suite in suites:
            run = TestRun(account_id=request.state.account_id, suite_id=suite.id, device_id=device.id,
                          status='pending', provider=config['provider'], model=config['model'])
            db.add(run); await db.flush()
            from core.test_accounts import select_run_account
            await select_run_account(db, run, body.test_accounts.get(f'{suite.id}:{device.id}', ''),
                                     connected_devices[device.id].platform)
            runs.append(run)
            db.add(RegressionTarget(plan_id=plan.id, run_id=run.id, suite_name=suite.name, device_name=device.name))
            for target in targets_by_suite[suite.id]:
                db.add(TestResult(run_id=run.id, case_id=target.node_id, status='pending', account_id=request.state.account_id))
    await db.commit()
    for run in runs:
        await start_tree_run(run.id, max_steps=body.max_steps)
    return await detail(plan, db)


@router.get('')
async def list_plans(request: Request, db: AsyncSession = Depends(get_db)):
    plans = (await db.execute(select(RegressionPlan).where(RegressionPlan.account_id == request.state.account_id).order_by(RegressionPlan.created_at.desc()).limit(100))).scalars().all()
    return [await detail(plan, db, summary=True) for plan in plans]


@router.get('/{plan_id}')
async def get_plan(plan_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await detail(await owned(db, RegressionPlan, plan_id, request), db)


@router.post('/{plan_id}/cancel')
async def cancel_plan(plan_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    plan = await owned(db, RegressionPlan, plan_id, request)
    rows = (await db.execute(select(RegressionTarget).where(RegressionTarget.plan_id == plan.id))).scalars().all()
    await asyncio.gather(*(cancel_run(row.run_id) for row in rows))
    return await detail(plan, db)


@router.post('/{plan_id}/retry', status_code=201)
async def retry_failed(plan_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    plan = await owned(db, RegressionPlan, plan_id, request)
    current = await detail(plan, db)
    if current['gate'] == 'waiting':
        raise HTTPException(409, '计划尚未结束')
    failed = [r for r in current['targets'] if not (r['status'] == 'done' and r['total'] > 0 and r['passed'] == r['total'])]
    if not failed:
        raise HTTPException(422, '没有失败目标')
    # Retry each failing cell exactly; avoid expanding the Cartesian product.
    config = current['config']
    new = RegressionPlan(account_id=request.state.account_id, name=plan.name + ' · 失败重跑', config_json=plan.config_json)
    db.add(new); await db.flush(); runs = []
    for row in failed:
        old = await db.get(TestRun, row['run_id'])
        if not old:
            raise HTTPException(422, '原运行已删除，无法重跑')
        await require_device(request, db, old.device_id)
        await require_suite(request, db, old.suite_id)
        conn = connected_devices.get(old.device_id)
        if not conn or not conn.is_connected:
            raise HTTPException(422, '设备离线：' + row['device_name'])
        await _validate_project_target(db, old.suite_id, old.device_id)
        targets = await node_targets_for_suite(db, old.suite_id)
        if not targets:
            raise HTTPException(422, '原套件已无可运行步骤')
        run = TestRun(account_id=request.state.account_id, suite_id=old.suite_id, device_id=old.device_id,
                      status='pending', provider=config['provider'], model=config['model'])
        db.add(run); await db.flush()
        from core.test_accounts import select_run_account
        selected_account = old.test_account_id or config.get('test_accounts', {}).get(f'{old.suite_id}:{old.device_id}', '')
        await select_run_account(db, run, selected_account, conn.platform)
        runs.append(run)
        db.add(RegressionTarget(plan_id=new.id, run_id=run.id, suite_name=row['suite_name'], device_name=row['device_name']))
        for target in targets:
            db.add(TestResult(run_id=run.id, case_id=target.node_id, status='pending', account_id=request.state.account_id))
    await db.commit()
    for run in runs:
        await start_tree_run(run.id, max_steps=config['max_steps'])
    return await detail(new, db)


@router.get('/{plan_id}/gate')
async def gate(plan_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    data = await detail(await owned(db, RegressionPlan, plan_id, request), db)
    return {'plan_id': plan_id, 'gate': data['gate'], 'release_allowed': data['gate'] == 'passed'}


@router.get('/{plan_id}/junit.xml')
async def junit(plan_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    plan = await owned(db, RegressionPlan, plan_id, request)
    data = await detail(plan, db)
    root = ET.Element('testsuites', name=plan.name)
    if data['gate'] == 'waiting':
        readiness = ET.SubElement(root, 'testsuite', name='plan readiness', tests='1', failures='0', errors='1')
        case = ET.SubElement(readiness, 'testcase', name='evidence finalization')
        ET.SubElement(case, 'error', message='waiting').text = '计划或证据归档尚未结束，禁止发布'
    for row in data['targets']:
        suite = ET.SubElement(root, 'testsuite', name=row['suite_name'] + ' @ ' + row['device_name'])
        results = (await db.execute(select(TestResult).where(TestResult.run_id == row['run_id']))).scalars().all()
        if row['status'] != 'done':
            case = ET.SubElement(suite, 'testcase', name='execution status')
            ET.SubElement(case, 'error', message=row['status']).text = '计划门禁未通过，运行尚未完整结束'
        for result in results:
            snapshots = data['config'].get('suite_snapshot', {}).get(row.get('suite_id'), [])
            snapshot = next((s for s in snapshots if s['node_id'] == result.case_id), None)
            case_name = ' > '.join(s['action'] for s in snapshot['steps']) if snapshot else result.case_id
            case = ET.SubElement(suite, 'testcase', name=case_name, classname=row['suite_name'])
            if result.status == 'fail': ET.SubElement(case, 'failure', message=result.reason).text = result.reason
            elif result.status != 'pass': ET.SubElement(case, 'error', message=result.reason or result.status).text = result.reason
        suite.set('tests', str(len(suite)))
        suite.set('failures', str(sum(c.find('failure') is not None for c in suite)))
        suite.set('errors', str(sum(c.find('error') is not None for c in suite)))
    return Response(ET.tostring(root, encoding='utf-8', xml_declaration=True), media_type='application/xml')


@router.get('/{plan_id}/evidence.zip')
async def evidence(plan_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    plan = await owned(db, RegressionPlan, plan_id, request)
    data = await detail(plan, db)
    if data['gate'] == 'waiting':
        raise HTTPException(409, '请等待计划结束再归档证据')
    output = tempfile.NamedTemporaryFile(prefix='sab-evidence-', suffix='.zip', delete=False)
    output.close()
    try:
        with zipfile.ZipFile(output.name, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('plan.json', json.dumps(data, ensure_ascii=False, indent=2))
            for row in data['targets']:
                results = (await db.execute(select(TestResult).where(TestResult.run_id == row['run_id']))).scalars().all()
                for result in results:
                    prefix = row['run_id'] + '/' + result.id
                    archive.writestr(prefix + '/execution.log', result.log or '')
                    if result.screenshot_b64:
                        archive.writestr(prefix + '/final.png', base64.b64decode(result.screenshot_b64))
                    steps = (await db.execute(select(TestStepLog).where(TestStepLog.result_id == result.id).order_by(TestStepLog.step))).scalars().all()
                    trace = []
                    for step in steps:
                        trace.append({'step': step.step, 'thought': step.thought, 'action': step.action, 'result': step.action_result,
                                      'perception_ms': step.perception_ms, 'llm_ms': step.llm_ms, 'action_ms': step.action_ms})
                        if step.screenshot_b64:
                            archive.writestr(prefix + '/step-' + str(step.step) + '.png', base64.b64decode(step.screenshot_b64))
                    archive.writestr(prefix + '/trace.json', json.dumps(trace, ensure_ascii=False, indent=2))
    except BaseException:
        Path(output.name).unlink(missing_ok=True)
        raise
    return FileResponse(output.name, media_type='application/zip', filename='regression-' + plan_id + '.zip',
                        background=BackgroundTask(Path(output.name).unlink, missing_ok=True))
