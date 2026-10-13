"""CRUD for Project Profiles (external KB / skills / source pointers)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from auth.access import require_operator
from core.projects import load_projects, new_project, save_projects, validate_kb_search_cmd, normalize_project
from db.database import AsyncSessionLocal

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectOut(BaseModel):
    id: str
    name: str
    app_package: str = ""
    app_ids: dict[str, str] = Field(default_factory=dict)
    kb_path: str = ""
    source_root: str = ""
    kb_search_cmd: str = ""


class ProjectIn(BaseModel):
    name: str = ""
    app_package: str = ""
    app_ids: dict[str, str] = Field(default_factory=dict)
    kb_path: str = ""
    source_root: str = ""
    kb_search_cmd: str = ""


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("", response_model=list[ProjectOut])
async def list_projects(request: Request, db: AsyncSession = Depends(get_db)):
    from auth.access import require_account_id, operator_account_id
    if require_account_id(request) != await operator_account_id(db):
        return []
    return load_projects()


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectIn, request: Request, db: AsyncSession = Depends(get_db)):
    await require_operator(request, db)
    projects = load_projects()
    try:
        proj = new_project(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    projects.append(proj)
    save_projects(projects)
    return proj


@router.put("/{project_id}", response_model=ProjectOut)
async def update_project(project_id: str, body: ProjectIn, request: Request, db: AsyncSession = Depends(get_db)):
    await require_operator(request, db)
    projects = load_projects()
    for p in projects:
        if p["id"] == project_id:
            values = normalize_project(body.model_dump())
            try:
                values["kb_search_cmd"] = validate_kb_search_cmd(values["kb_search_cmd"])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            p.update(values)
            save_projects(projects)
            return p
    raise HTTPException(status_code=404, detail="Project not found")


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await require_operator(request, db)
    projects = load_projects()
    remaining = [p for p in projects if p["id"] != project_id]
    if len(remaining) == len(projects):
        raise HTTPException(status_code=404, detail="Project not found")
    save_projects(remaining)
