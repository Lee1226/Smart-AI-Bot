"""Structured, reviewable skills derived from AI exploration traces."""
from datetime import datetime
import uuid

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ReusableSkill(Base):
    __tablename__ = "reusable_skills"
    __table_args__ = (
        Index("ix_reusable_skills_account_status", "account_id", "status"),
        Index("ix_reusable_skills_account_source", "account_id", "source_session_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(String, index=True)
    source_session_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String, default="general")
    platform: Mapped[str] = mapped_column(String, default="")
    app_package: Mapped[str] = mapped_column(String, default="")
    preconditions_json: Mapped[str] = mapped_column(Text, default="[]")
    recognition_json: Mapped[str] = mapped_column(Text, default="{}")
    steps_json: Mapped[str] = mapped_column(Text, default="[]")
    branches_json: Mapped[str] = mapped_column(Text, default="[]")
    postconditions_json: Mapped[str] = mapped_column(Text, default="[]")
    safety_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String, default="draft")
    version: Mapped[int] = mapped_column(default=1)
    review_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
