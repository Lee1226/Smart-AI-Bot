import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, Float, String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


QUICK_SUITE_FORMAT = "quick"
QUICK_SUITE_PREFIX = "快速任务:"


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    token: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, default="Unknown")
    connection_type: Mapped[str] = mapped_column(String, default="portal")
    status: Mapped[str] = mapped_column(String, default="offline")  # online/offline
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    account_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class IosHost(Base):
    """Account-scoped Mac helper; never stores the helper's bearer token."""
    __tablename__ = "ios_hosts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    devices_json: Mapped[str] = mapped_column(Text, default='{}')


class TestSuite(Base):
    """A parsed test file (XMind or Markdown)."""
    __tablename__ = "test_suites"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String)
    source_format: Mapped[str] = mapped_column(String)  # xmind / markdown
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Target app package — used to match an optional Project Profile (external
    # KB / skills / source) so the agent gets project-specific context.
    app_package: Mapped[str] = mapped_column(String, default="")
    project_id: Mapped[str] = mapped_column(String, default="")
    account_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # User-defined grouping tag (e.g. "登录", "支付", "回归") — empty = ungrouped.
    tag: Mapped[str] = mapped_column(String, default="")

    cases: Mapped[list["TestCase"]] = relationship(back_populates="suite", cascade="all, delete-orphan")
    runs: Mapped[list["TestRun"]] = relationship(back_populates="suite", cascade="all, delete-orphan")


class TestCase(Base):
    """A single test case extracted from a suite."""
    __tablename__ = "test_cases"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    suite_id: Mapped[str] = mapped_column(ForeignKey("test_suites.id"))
    path: Mapped[str] = mapped_column(Text)       # "Module > Scenario > Condition"
    expected: Mapped[str] = mapped_column(Text, default="")   # leaf node / last segment
    order: Mapped[int] = mapped_column(Integer, default=0)
    parameters: Mapped[str] = mapped_column(Text, default="")  # JSON: [{"key":"val"}, ...] for parameterized runs
    # JSON list of {action, expected} — when non-empty the runner verifies
    # each step in order and fails the case on the first miss.  Empty for
    # legacy single-expected cases.
    checkpoints: Mapped[str] = mapped_column(Text, default="")
    # Inherently repetitive task (e.g. a 100-question quiz): repeats the same
    # action while making real progress. Skips the L4 stuck-loop backstop and
    # relies on max_steps instead. See docs/agent-architecture.md.
    loop_task: Mapped[bool] = mapped_column(Boolean, default=False)
    account_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Deterministic replay: raw recorded actions (JSON list of
    # {kind, x, y, text, direction, action, package, delay_ms}) captured at
    # record time. When present, a run in "replay" mode executes these verbatim
    # (coordinates + timing) without per-step AI decisions.
    replay_steps: Mapped[str] = mapped_column(Text, default="")

    suite: Mapped["TestSuite"] = relationship(back_populates="cases")


class StepNode(Base):
    """One step in a suite's step-tree. A test case = a root→node path.

    Shared prefixes are a single node (true reuse): every case passing through
    a node references the same row. See the step-tree design spec.
    """
    __tablename__ = "step_nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    suite_id: Mapped[str] = mapped_column(ForeignKey("test_suites.id"), index=True)
    parent_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("step_nodes.id"), nullable=True, index=True
    )  # null = root step
    action: Mapped[str] = mapped_column(Text, default="")       # 行为: what this step does
    expected: Mapped[str] = mapped_column(Text, default="")     # 期望 (optional); "" = success-is-done
    order: Mapped[int] = mapped_column(Integer, default=0)      # sibling order
    reversible: Mapped[bool] = mapped_column(Boolean, default=True)   # Phase 2: back-navigable
    loop_task: Mapped[bool] = mapped_column(Boolean, default=False)   # repetitive task
    parameters: Mapped[str] = mapped_column(Text, default="")         # JSON preset test data
    # Case library (Phase 3):
    #   source_id — provenance for a SNAPSHOT copy (which node it was copied from)
    #   ref_id    — a LIVE LINK: this node resolves to ref_id's node + subtree
    source_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ref_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    account_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class TestRun(Base):
    """One execution of a full suite against a device."""
    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    suite_id: Mapped[str] = mapped_column(ForeignKey("test_suites.id"))
    device_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/running/done
    provider: Mapped[str] = mapped_column(String, default="")
    model: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    account_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Optional per-run webhook URL — if set, POST structured result here on completion.
    webhook_url: Mapped[str] = mapped_column(String, default="")
    test_account_id: Mapped[str] = mapped_column(String, default="")
    test_account_name: Mapped[str] = mapped_column(String, default="")

    suite: Mapped["TestSuite"] = relationship(back_populates="runs")
    results: Mapped[list["TestResult"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class TestResult(Base):
    """Result of a single test case within a run."""
    __tablename__ = "test_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id"))
    case_id: Mapped[str] = mapped_column(ForeignKey("test_cases.id"))
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/pass/fail/error/skip
    reason: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[int] = mapped_column(Integer, default=0)
    screenshot_b64: Mapped[str] = mapped_column(Text, default="")  # final screenshot
    log: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False)
    action_history_json: Mapped[str] = mapped_column(Text, default="")  # JSON list of {step,fn_name,args,result}
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    account_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    run: Mapped["TestRun"] = relationship(back_populates="results")
    step_logs: Mapped[list["TestStepLog"]] = relationship(back_populates="result", cascade="all, delete-orphan")


class TestStepLog(Base):
    """One agent step within a test result — screenshot + action for replay."""
    __tablename__ = "test_step_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    result_id: Mapped[str] = mapped_column(ForeignKey("test_results.id"), index=True)
    step: Mapped[int] = mapped_column(Integer)
    thought: Mapped[str] = mapped_column(Text, default="")   # AI reasoning before acting
    action: Mapped[str] = mapped_column(Text, default="")    # "tap_element({'index': 7})"
    action_result: Mapped[str] = mapped_column(Text, default="")  # "Tapped element 7 at (550,200)"
    screenshot_b64: Mapped[str] = mapped_column(Text, default="")  # annotated screenshot

    # Token usage
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # Timing (milliseconds)
    perception_ms: Mapped[int] = mapped_column(Integer, default=0)  # screenshot + UI tree + annotate
    llm_ms: Mapped[int] = mapped_column(Integer, default=0)         # LLM call duration
    action_ms: Mapped[int] = mapped_column(Integer, default=0)      # tool dispatch duration

    # Subgoal context (null for single-agent runs)
    subgoal_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    subgoal_desc: Mapped[str] = mapped_column(Text, default="")
    account_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    result: Mapped["TestResult"] = relationship(back_populates="step_logs")


class LessonLearned(Base):
    """Negative experience extracted from past runs — injected to avoid repeating mistakes."""
    __tablename__ = "lessons_learned"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    case_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)  # specific case, or null for global
    suite_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)  # for suite-level lessons
    app_package: Mapped[str] = mapped_column(String, default="")   # e.g. "com.example.app"
    task_keyword: Mapped[str] = mapped_column(String, default="")  # key part of path for fuzzy matching across quick runs
    screen_context: Mapped[str] = mapped_column(String, default="")  # e.g. "Settings detail page"
    lesson: Mapped[str] = mapped_column(Text, default="")  # e.g. "Do not tap the bottom chat input — it opens the keyboard"
    source_run_id: Mapped[str] = mapped_column(String, default="")
    source_step: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    account_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)


# ── Multi-tenancy: Accounts & Users ──────────────────────────────────────────


class Account(Base):
    """A tenant / team — owns all resources (devices, suites, runs, …)."""
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Installation-level operator: owns global settings / releases / project profiles.
    # Only one account should have this set; auth resolves it (env pin → this flag → oldest).
    is_operator: Mapped[bool] = mapped_column(Boolean, default=False)

    users: Mapped[list["User"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class AccountSettings(Base):
    """Per-account settings (LLM keys / default model / language / webhook).

    Isolated from the operator's global secrets — each account configures and
    uses its own keys.  JSON blob mirrors the legacy global settings.json shape.
    """
    __tablename__ = "account_settings"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    data: Mapped[str] = mapped_column(Text, default="{}")   # JSON-serialized settings dict
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class User(Base):
    """A login user belonging to exactly one account."""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String, default="")
    role: Mapped[str] = mapped_column(String, default="member")  # owner / admin / member
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    account: Mapped["Account"] = relationship(back_populates="users")


class ApiKey(Base):
    """API key for programmatic access — belongs to an account, created by a user."""
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String, default="")          # human label
    key_hash: Mapped[str] = mapped_column(String, unique=True)     # sha256(full_key)
    key_prefix: Mapped[str] = mapped_column(String, default="")    # sk-XXXX… for UI display
    permissions: Mapped[str] = mapped_column(Text, default="[]")   # JSON list, reserved
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    account: Mapped["Account"] = relationship(back_populates="api_keys")


class ProjectTestAccount(Base):
    __tablename__ = "project_test_accounts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(String, index=True)
    project_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    environment: Mapped[str] = mapped_column(String, default="测试服")
    platforms: Mapped[str] = mapped_column(Text, default='["android", "harmony"]')
    login_mode: Mapped[str] = mapped_column(String, default="phone")
    username_enc: Mapped[str] = mapped_column(Text, default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    verification_code_enc: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class TestAccountLease(Base):
    __tablename__ = "test_account_leases"
    test_account_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
