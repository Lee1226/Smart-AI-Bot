"""Encrypted project login credentials; secrets never need to enter AI prompts."""
from __future__ import annotations

import json
import base64
import os
from dataclasses import fields, is_dataclass
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from db.database import AsyncSessionLocal, DB_PATH
from db.models import ProjectTestAccount, TestAccountLease, TestRun, TestSuite


def cipher() -> Fernet:
    path = Path(os.getenv('SAB_TEST_ACCOUNT_KEY_PATH', str(DB_PATH.parent / '.test_account_key')))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as f:
            f.write(Fernet.generate_key())
    path.chmod(0o600)
    return Fernet(path.read_bytes())


def encrypt(value: str) -> str:
    return cipher().encrypt(value.encode()).decode() if value else ''


def decrypt(value: str) -> str:
    return cipher().decrypt(value.encode()).decode() if value else ''


def masked(value: str) -> str:
    return value[:2] + '***' + value[-2:] if len(value) > 5 else '***' if value else ''


def public_account(row) -> dict:
    return dict(id=row.id, project_id=row.project_id, name=row.name,
                environment=row.environment, platforms=json.loads(row.platforms),
                login_mode=row.login_mode, username_masked=masked(decrypt(row.username_enc)),
                password_configured=bool(row.password_enc),
                verification_code_configured=bool(row.verification_code_enc), notes=row.notes,
                is_default=row.is_default)


class LoginContext:
    def __init__(self, row):
        self.name = row.name or ""
        self.environment = row.environment
        self.notes = row.notes or ""
        self.login_mode = row.login_mode
        self.username = decrypt(row.username_enc)
        self.password = decrypt(row.password_enc)
        self.verification_code = decrypt(getattr(row, "verification_code_enc", "") or "")

    def redact(self, value):
        if isinstance(value, str):
            for secret in sorted({self.username, self.password, self.verification_code} - {''}, key=len, reverse=True):
                # Include JSON-escaped values in persisted tool-call strings.
                value = value.replace(json.dumps(secret)[1:-1], '[测试凭据已隐藏]')
                value = value.replace(secret, '[测试凭据已隐藏]')
                value = value.replace(base64.b64encode(secret.encode()).decode(), '[测试凭据已隐藏]')
            return value
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if isinstance(value, dict):
            return {k: self.redact(v) for k, v in value.items()}
        if is_dataclass(value):
            for f in fields(value):
                if f.name not in {'screenshot_b64'}:
                    setattr(value, f.name, self.redact(getattr(value, f.name)))
        return value

    def prompt(self):
        route = {
            'phone': 'Use the phone-number login entry and the phone field. This is SMS verification-code login, not password login.',
            'email': 'Switch to the email login entry and use the email field, not the phone field. This is email and password login. Focus the email field and call input_test_credential(field=username), then focus the password field and call input_test_credential(field=password).',
            'password': 'Use password login only if the app actually offers it; otherwise report the limitation.',
        }.get(self.login_mode, 'Login mode is not configured. Request an updated test account configuration.')
        if self.login_mode == 'phone':
            route += (' Focus the phone field and call input_test_credential(field=username), then focus the verification-code field and call input_test_credential(field=verification_code). A code is configured; try it before reporting login blocked.' if self.verification_code else ' No verification code is configured; fill the phone number, then report that a usable code must be configured if login requires it.')
        return self.redact(
            f'[Selected test account] {self.name}; environment={self.environment}; '
            f'login mode={self.login_mode}; identity hint={masked(self.username)}. Notes: {self.notes}\n'
            f'{route}\n'
            'Login is a precondition. Check existing signed-in identity before proceeding; '
            'if identity cannot be confirmed, report it rather than assume it matches. '
            'To log in, first focus the correct field, then call input_test_credential '
            'with field=username, password or verification_code. Credentials are filled locally; never guess them. '
            'If the configured verification code is rejected or expired, report that a new code is needed; '
            'do not send SMS repeatedly or invent a code. Do not log out or change account '
            'unless the case explicitly requires it.'
        )


async def check_available(db, account_id: str):
    lease = await db.get(TestAccountLease, account_id)
    if lease:
        run = await db.get(TestRun, lease.run_id)
        if run and run.status in {'pending', 'queued', 'preparing', 'running'}:
            raise HTTPException(409, '测试账号正在其他任务中使用，请等待或选择其他账号')
        await db.delete(lease)
        await db.flush()


async def select_run_account(db, run, requested: str, platform: str):
    """Called before committing each run; a DB primary key prevents double leasing."""
    from core.projects import suite_project
    from auth.access import operator_account_id
    suite = await db.get(TestSuite, run.suite_id)
    if requested == '-':
        return
    if suite.account_id != await operator_account_id(db):
        if requested:
            raise HTTPException(403, '当前账号不能使用实例项目测试账号')
        return
    project = suite_project(suite)
    if not project:
        if requested:
            raise HTTPException(400, '请先关联项目再选择测试账号')
        return
    query = select(ProjectTestAccount).where(
        ProjectTestAccount.account_id == run.account_id,
        ProjectTestAccount.project_id == project['id'], ProjectTestAccount.active.is_(True))
    query = query.where(ProjectTestAccount.id == requested) if requested else query.where(ProjectTestAccount.is_default.is_(True))
    row = (await db.execute(query)).scalar_one_or_none()
    if not row:
        if requested:
            raise HTTPException(400, '测试账号不存在或不属于当前项目')
        return
    if platform not in json.loads(row.platforms):
        raise HTTPException(400, '测试账号不适用于当前设备平台，请选择其他账号')
    await check_available(db, row.id)
    run.test_account_id = row.id
    run.test_account_name = row.name
    db.add(TestAccountLease(test_account_id=row.id, run_id=run.id))
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, '测试账号已被其他任务占用')


async def context_for_run(run_id: str):
    async with AsyncSessionLocal() as db:
        run = await db.get(TestRun, run_id)
        if not run or not run.test_account_id:
            return None
        row = await db.get(ProjectTestAccount, run.test_account_id)
        if not row or not row.active or row.account_id != run.account_id:
            raise ValueError('所选测试账号不可用')
        return LoginContext(row)


async def release_run_account(run_id: str):
    async with AsyncSessionLocal() as db:
        await db.execute(delete(TestAccountLease).where(TestAccountLease.run_id == run_id))
        await db.commit()
