"""Account-scoped audio corpus metadata used by execution-plan audio steps."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class AudioCorpus(Base):
    __tablename__ = "audio_corpus"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    original_filename: Mapped[str] = mapped_column(String, default="")
    storage_path: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(String, default="")
    mime_type: Mapped[str] = mapped_column(String, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    transcript: Mapped[str] = mapped_column(Text, default="")
    expected_result: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String, default="general")
    language: Mapped[str] = mapped_column(String, default="zh-CN")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
