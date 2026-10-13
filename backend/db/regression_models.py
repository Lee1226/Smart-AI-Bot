import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from db.database import Base


class RegressionArtifact(Base):
    __tablename__ = 'regression_artifacts'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    account_id: Mapped[str] = mapped_column(String, index=True)
    filename: Mapped[str] = mapped_column(String)
    version_label: Mapped[str] = mapped_column(String, default='')
    sha256: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RegressionPlan(Base):
    __tablename__ = 'regression_plans'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    account_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    config_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RegressionTarget(Base):
    __tablename__ = 'regression_targets'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    plan_id: Mapped[str] = mapped_column(ForeignKey('regression_plans.id'), index=True)
    run_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    suite_name: Mapped[str] = mapped_column(String)
    device_name: Mapped[str] = mapped_column(String)
    preparation_json: Mapped[str] = mapped_column(Text, default='{}')
    diagnostics_json: Mapped[str] = mapped_column(Text, default='{}')
