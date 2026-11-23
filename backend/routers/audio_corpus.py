"""Account-scoped audio corpus upload, listing and playback-device APIs."""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.access import require_account_id
from core.audio_player import list_output_devices
from db.audio_models import AudioCorpus
from db.database import AsyncSessionLocal

router = APIRouter(prefix="/api/audio-corpus", tags=["audio-corpus"])

MAX_AUDIO_BYTES = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg"}
AUDIO_ROOT = Path(__file__).resolve().parent.parent / "data" / "audio_corpus"


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


class AudioCorpusOut(BaseModel):
    id: str
    name: str
    original_filename: str
    format: str
    mime_type: str
    size_bytes: int
    duration_seconds: float
    transcript: str
    expected_result: str
    category: str
    language: str
    created_at: str


def _out(item: AudioCorpus) -> AudioCorpusOut:
    return AudioCorpusOut(
        id=item.id,
        name=item.name,
        original_filename=item.original_filename,
        format=item.format,
        mime_type=item.mime_type,
        size_bytes=item.size_bytes,
        duration_seconds=item.duration_seconds,
        transcript=item.transcript or "",
        expected_result=item.expected_result or "",
        category=item.category or "general",
        language=item.language or "zh-CN",
        created_at=item.created_at.isoformat(),
    )


def _account_storage_dir(account_id: str) -> Path:
    account_key = hashlib.sha256(account_id.encode("utf-8")).hexdigest()[:24]
    return AUDIO_ROOT / account_key


@router.get("", response_model=list[AudioCorpusOut])
async def list_audio_corpus(
    request: Request,
    q: str = "",
    db: AsyncSession = Depends(get_db),
):
    account_id = require_account_id(request)
    statement = select(AudioCorpus).where(AudioCorpus.account_id == account_id)
    if q.strip():
        keyword = f"%{q.strip()}%"
        statement = statement.where(
            (AudioCorpus.name.ilike(keyword))
            | (AudioCorpus.original_filename.ilike(keyword))
            | (AudioCorpus.transcript.ilike(keyword))
        )
    rows = (await db.execute(statement.order_by(AudioCorpus.created_at.desc()))).scalars().all()
    return [_out(row) for row in rows]


@router.get("/output-devices")
async def audio_output_devices(request: Request):
    require_account_id(request)
    try:
        devices = await asyncio.to_thread(list_output_devices)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"devices": devices}


@router.post("", response_model=AudioCorpusOut, status_code=201)
async def upload_audio_corpus(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(""),
    transcript: str = Form(""),
    expected_result: str = Form(""),
    category: str = Form("general"),
    language: str = Form("zh-CN"),
    db: AsyncSession = Depends(get_db),
):
    account_id = require_account_id(request)
    original_filename = Path(file.filename or "audio").name
    extension = Path(original_filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="仅支持 WAV、MP3、FLAC、OGG 音频文件")

    content = await file.read(MAX_AUDIO_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="音频文件为空")
    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="音频文件不能超过 50 MB")

    item_id = __import__("uuid").uuid4().hex
    directory = _account_storage_dir(account_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{item_id}{extension}"
    target.write_bytes(content)

    try:
        import soundfile as sf
        info = await asyncio.to_thread(sf.info, str(target))
        duration = float(info.duration)
        if duration <= 0:
            raise ValueError("音频时长无效")
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=415, detail=f"音频格式无法解码或文件已损坏：{exc}") from exc

    item = AudioCorpus(
        id=item_id,
        account_id=account_id,
        name=(name or original_filename).strip()[:200],
        original_filename=original_filename[:255],
        storage_path=str(target),
        format=extension.lstrip("."),
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=len(content),
        duration_seconds=round(duration, 3),
        transcript=transcript.strip(),
        expected_result=expected_result.strip(),
        category=(category or "general").strip()[:80],
        language=(language or "zh-CN").strip()[:32],
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return _out(item)


@router.get("/{audio_id}/file")
async def download_audio_corpus(audio_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    account_id = require_account_id(request)
    item = (await db.execute(select(AudioCorpus).where(
        AudioCorpus.id == audio_id, AudioCorpus.account_id == account_id
    ))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="音频语料不存在")
    path = Path(item.storage_path).resolve()
    if not path.is_file() or not path.is_relative_to(AUDIO_ROOT.resolve()):
        raise HTTPException(status_code=404, detail="音频文件不存在")
    return FileResponse(path, media_type=item.mime_type, filename=item.original_filename)


@router.delete("/{audio_id}", status_code=204)
async def delete_audio_corpus(audio_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    account_id = require_account_id(request)
    item = (await db.execute(select(AudioCorpus).where(
        AudioCorpus.id == audio_id, AudioCorpus.account_id == account_id
    ))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="音频语料不存在")
    path = Path(item.storage_path)
    path.unlink(missing_ok=True)
    await db.delete(item)
    await db.commit()
