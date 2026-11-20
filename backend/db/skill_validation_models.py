"""Immutable structural validation history for reusable skills."""
from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class SkillValidationRun(Base):
    __tablename__ = "skill_validation_runs"
    __table_args__ = (
        Index("ix_skill_validation_skill_created", "skill_id", "created_at"),
        Index("ix_skill_validation_account_created", "account_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(String, index=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("reusable_skills.id", ondelete="CASCADE"), index=True)
    skill_version: Mapped[int] = mapped_column(Integer, default=1)
    validation_type: Mapped[str] = mapped_column(String, default="structural")
    status: Mapped[str] = mapped_column(String, default="failed")
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    findings_json: Mapped[str] = mapped_column(Text, default="[]")
    skill_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    device_touched: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
