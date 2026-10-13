"""Project-scoped test login accounts, deliberately separate from SAB user accounts."""
import json
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from auth.access import require_operator, require_account_id
from core.projects import get_project
from core.test_accounts import encrypt, public_account, check_available
from db.database import AsyncSessionLocal
from db.models import ProjectTestAccount

class SafeAccountRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI validation normally echoes submitted values, including passwords.
                raise HTTPException(422, '测试账号配置不完整或格式不正确，请检查名称、凭据和平台')
        return safe


router = APIRouter(route_class=SafeAccountRoute, prefix='/api/projects/{project_id}/test-accounts', tags=['test accounts'])


async def get_db():
    async with AsyncSessionLocal() as db:
        yield db


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    environment: str = Field(default='测试服', min_length=1, max_length=100)
    platforms: list[Literal['android', 'harmony', 'ios']] = Field(default_factory=lambda: ['android', 'harmony'], min_length=1)
    login_mode: Literal['phone', 'email', 'password'] = 'phone'
    username: Optional[str] = Field(default=None, max_length=500)
    password: Optional[str] = Field(default=None, max_length=500)
    verification_code: Optional[str] = Field(default=None, max_length=500)
    notes: str = Field(default='', max_length=2000)
    is_default: bool = False


async def project_access(project_id, request, db):
    await require_operator(request, db)
    if not get_project(project_id):
        raise HTTPException(404, 'Project not found')


async def owned_row(project_id, row_id, request, db):
    await project_access(project_id, request, db)
    row = (await db.execute(select(ProjectTestAccount).where(
        ProjectTestAccount.id == row_id, ProjectTestAccount.project_id == project_id,
        ProjectTestAccount.account_id == require_account_id(request), ProjectTestAccount.active.is_(True)))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, 'Test account not found')
    return row


@router.get('')
async def list_accounts(project_id: str, request: Request, db=Depends(get_db)):
    await project_access(project_id, request, db)
    rows = (await db.execute(select(ProjectTestAccount).where(
        ProjectTestAccount.project_id == project_id,
        ProjectTestAccount.account_id == require_account_id(request), ProjectTestAccount.active.is_(True)))).scalars().all()
    return [public_account(r) for r in rows]


async def apply_values(row, body, db):
    name = body.name.strip()
    if not name or not body.environment.strip():
        raise HTTPException(422, '账号名称和环境不能为空')
    if body.username is not None:
        row.username_enc = encrypt(body.username.strip())
    if body.password is not None:
        row.password_enc = encrypt(body.password)
    if body.verification_code is not None:
        row.verification_code_enc = encrypt(body.verification_code.strip())
    if body.login_mode != "phone":
        row.verification_code_enc = ""
    else:
        row.password_enc = ""
    if not row.username_enc:
        raise HTTPException(422, '请填写登录账号')
    if body.login_mode == 'email':
        from core.test_accounts import decrypt
        import re
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', decrypt(row.username_enc)):
            raise HTTPException(422, '请填写有效邮箱地址')
    if body.login_mode in {'password', 'email'} and not row.password_enc:
        raise HTTPException(422, '密码登录需填写密码')
    if body.is_default:
        await db.execute(update(ProjectTestAccount).where(
            ProjectTestAccount.project_id == row.project_id, ProjectTestAccount.account_id == row.account_id
        ).values(is_default=False))
    row.name = name
    row.environment = body.environment.strip()
    row.platforms = json.dumps(body.platforms)
    row.login_mode = body.login_mode
    # Notes must not become a second plaintext credential store.
    from core.test_accounts import LoginContext
    row.notes = body.notes
    context = LoginContext(row)
    row.name = context.redact(row.name)
    row.environment = context.redact(row.environment)
    row.notes = context.redact(body.notes)
    row.is_default = body.is_default


@router.post('', status_code=201)
async def create_account(project_id: str, body: AccountIn, request: Request, db=Depends(get_db)):
    await project_access(project_id, request, db)
    row = ProjectTestAccount(project_id=project_id, account_id=require_account_id(request),
                             name=body.name, username_enc='', password_enc='', verification_code_enc='')
    await apply_values(row, body, db)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return public_account(row)


@router.put('/{row_id}')
async def edit_account(project_id: str, row_id: str, body: AccountIn, request: Request, db=Depends(get_db)):
    row = await owned_row(project_id, row_id, request, db)
    await check_available(db, row.id)
    await apply_values(row, body, db)
    await db.commit()
    await db.refresh(row)
    return public_account(row)


@router.delete('/{row_id}', status_code=204)
async def delete_account(project_id: str, row_id: str, request: Request, db=Depends(get_db)):
    row = await owned_row(project_id, row_id, request, db)
    await check_available(db, row.id)
    row.active = False
    row.is_default = False
    row.username_enc = row.password_enc = row.verification_code_enc = ''
    await db.commit()
