"""Persistent data models for AI exploration sessions and reusable knowledge."""
from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ExploreSession(Base):
    __tablename__ = "explore_sessions"
    __table_args__ = (
        Index("ix_explore_sessions_account_created", "account_id", "created_at"),
        Index("ix_explore_sessions_account_status", "account_id", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String, default="")
    goal: Mapped[str] = mapped_column(Text)
    expected_result: Mapped[str] = mapped_column(Text, default="")
    device_id: Mapped[str] = mapped_column(String, index=True)
    platform: Mapped[str] = mapped_column(String, default="android")
    app_package: Mapped[str] = mapped_column(String, default="")
    model_id: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="draft")
    max_steps: Mapped[int] = mapped_column(Integer, default=20)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    summary: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ExploreStep(Base):
    __tablename__ = "explore_steps"
    __table_args__ = (
        Index("ix_explore_steps_session_sequence", "session_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(String, index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("explore_sessions.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String, default="action")
    title: Mapped[str] = mapped_column(String, default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(String, default="")
    executor: Mapped[str] = mapped_column(String, default="deterministic")
    action_args_json: Mapped[str] = mapped_column(Text, default="{}")
    page_before_json: Mapped[str] = mapped_column(Text, default="{}")
    page_after_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String, default="recorded")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ExploreKnowledge(Base):
    __tablename__ = "explore_knowledge"
    __table_args__ = (
        Index("ix_explore_knowledge_account_status", "account_id", "status"),
        Index("ix_explore_knowledge_account_category", "account_id", "category"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(String, index=True)
    source_session_id: Mapped[str] = mapped_column(String, default="", index=True)
    title: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String, default="other")
    description: Mapped[str] = mapped_column(Text, default="")
    trigger_conditions_json: Mapped[str] = mapped_column(Text, default="{}")
    recognition_rules_json: Mapped[str] = mapped_column(Text, default="{}")
    handling_rules_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    app_package: Mapped[str] = mapped_column(String, default="")
    platform: Mapped[str] = mapped_column(String, default="")
    risk_level: Mapped[str] = mapped_column(String, default="normal")
    status: Mapped[str] = mapped_column(String, default="discovered")
    confirmed_by: Mapped[str] = mapped_column(String, default="")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
