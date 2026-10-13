"""
Orchestrates a full test run: iterates over all TestCase rows for a TestRun,
executes each via TestCaseAgent, persists results, and streams live logs.

Log streaming uses RunState (history buffer + asyncio.Condition) so that:
- Any number of SSE consumers can subscribe.
- Reconnecting consumers replay the full history from the beginning.
- Cancellation propagates cleanly to the background task.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import AsyncGenerator, List, Optional

from sqlalchemy import select, update

from agent.ws_device import WebSocketDevice
from core.run_memory import (
    extract_lessons,
    load_lessons,
    load_reference_examples,
    task_keyword_for,
)
from core.run_recorder import RunRecorder
from core.step_tree import NodeRow, RunTarget, backtrack_plan, chain_to_node, dfs_run_targets, flatten_chain, resolve_links
from core.test_agent import CaseResult, TestCaseAgent
from core.test_parser import Step, TestCaseData
from agent.verifier import LLMVerifier
from db.database import AsyncSessionLocal
from db.models import StepNode, TestCase, TestResult, TestRun, TestStepLog
from core.settings_store import (
    apply_aws_env,
    build_fallback_chain,
    load_settings,
    resolve_api_base,
    resolve_api_key,
    resolve_language,
    resolve_verifier,
)
from ws.portal_ws import connected_devices

logger = logging.getLogger(__name__)


def _missing_key(provider: str, api_key: str) -> bool:
    """True when a run would send an empty key (ollama is the only keyless provider)."""
    return not api_key and (provider or "").lower() != "ollama"


async def _mark_error(run_id: str, reason: str) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(TestRun).where(TestRun.id == run_id)
            .values(status="error", finished_at=datetime.utcnow())
        )
        await session.execute(
            update(TestResult).where(TestResult.run_id == run_id, TestResult.status == "pending")
            .values(status="error", reason=reason, finished_at=datetime.utcnow())
        )
        await session.commit()


# ── RunState ─────────────────────────────────────────────────────────────────

class RunState:
    """
    Holds live state for one in-progress run.
    Multiple SSE consumers can call stream() concurrently; each gets the full
    history starting from log[0], then follows new lines live.
    """

    def __init__(self, run_id: str = "") -> None:
        self.run_id = run_id
        self.redact = lambda value: value
        self.logs: List[str] = []
        self.done: bool = False
        self.task: Optional[asyncio.Task] = None
        self.cond: asyncio.Condition = asyncio.Condition()

    async def emit(self, msg: str) -> None:
        msg = self.redact(msg)
        async with self.cond:
            self.logs.append(msg)
            self.cond.notify_all()

    async def finish(self) -> None:
        if self.run_id:
            from core.test_accounts import release_run_account
            await release_run_account(self.run_id)
        async with self.cond:
            self.done = True
            self.cond.notify_all()

    async def stream(self) -> AsyncGenerator[str, None]:
        """Replay all buffered logs then follow live until done."""
        idx = 0
        while True:
            async with self.cond:
                # Wait until there is something new to send or the run is done
                await self.cond.wait_for(lambda: idx < len(self.logs) or self.done)
                snapshot_len = len(self.logs)
                is_done = self.done
            # Send everything up to snapshot
            while idx < snapshot_len:
                yield f"data: {self.logs[idx]}\n\n"
                idx += 1
            if is_done and idx >= snapshot_len:
                yield "data: [done]\n\n"
                return


# Registry of active run states
active_runs: dict[str, RunState] = {}


# ── Project Profile KB ─────────────────────────────────────────────────────────

async def _suite_app_package(suite_id: str, platform: str = "android") -> str:
    from core.projects import target_for_suite
    from db.models import TestSuite
    from auth.access import operator_account_id
    async with AsyncSessionLocal() as session:
        suite = await session.get(TestSuite, suite_id)
        if not suite:
            return ""
        if suite.account_id != await operator_account_id(session):
            if suite.project_id:
                raise ValueError("当前账号不能使用实例项目")
            if suite.app_package and platform != "android":
                raise ValueError("旧版包名仅适用于 Android，请配置对应平台的项目应用标识")
            return suite.app_package or ""
        return target_for_suite(suite, platform)


async def _operator_project_app_package(suite_id: str) -> str:
    """Global project profiles are installation-level and never cross tenants."""
    try:
        from auth.access import operator_account_id
        from db.models import TestSuite
        async with AsyncSessionLocal() as session:
            suite = await session.get(TestSuite, suite_id)
            if not suite or suite.account_id != await operator_account_id(session):
                return ""
            return suite.project_id or suite.app_package or ""
    except Exception:
        return ""


async def _force_stop_app_after_run(device, package: str, emit) -> None:
    """Force-stop the tested app once a run finishes, without blind gestures.

    The configured suite package wins: a permission dialog or external handoff
    must not make cleanup stop a system package instead of the app under test.
    Best-effort cleanup never changes the already-computed run result.
    """
    try:
        current_package = await device.current_package()
        target_package = package or current_package
        if not target_package:
            await emit("(could not force-stop app: target package is unknown)")
            return
        result = await device.stop_app(target_package)
        await emit(f"🛑 {result}")
    except Exception as exc:
        label = package or "current app"
        await emit(f"(could not force-stop {label}: {exc})")


async def _project_kb_roots(suite_id: str) -> list:
    """Extra KB dirs from the Project Profile matching this suite's app_package.

    Returns [] when the suite has no app_package, no profile matches, or the
    profile's kb_path is missing — so runs behave exactly as before."""
    try:
        from core.projects import kb_roots_for
        return kb_roots_for(await _operator_project_app_package(suite_id))
    except Exception:
        return []


async def _project_source_root(suite_id: str) -> str:
    """App source root from the matched Project Profile ('' when none/missing)."""
    try:
        from core.projects import source_root_for
        return source_root_for(await _operator_project_app_package(suite_id))
    except Exception:
        return ""


async def _project_kb_search_cmd(suite_id: str) -> str:
    """Knowledge-search CLI from the matched Project Profile ('' when none)."""
    try:
        from core.projects import kb_search_cmd_for
        return kb_search_cmd_for(await _operator_project_app_package(suite_id))
    except Exception:
        return ""


# ── Public API ────────────────────────────────────────────────────────────────

async def start_run(run_id: str, max_steps: int = 20, step_delay: float = 1.0,
                    max_retries: int = 0, mode: str = "ai") -> None:
    """Register a RunState and launch the run as a background task.

    mode: "ai" (default, LLM drives each step) or "replay" (deterministic
    playback of recorded actions, single AI verification at the end).
    """
    state = RunState(run_id)
    executor = execute_replay_run if mode == "replay" else execute_run
    from core.device_scheduler import enqueue
    await enqueue(run_id, state, lambda: executor(run_id, state, max_steps=max_steps, step_delay=step_delay, max_retries=max_retries))


# ── Folder batch run (one session, many checks) ──────────────────────────────

_DESTRUCTIVE_KW = ("退出登录", "登出", "注销账号", "删除账号", "logout", "log out", "sign out")


def _is_destructive(text: str) -> bool:
    t = (text or "").lower()
    return any(kw.lower() in t for kw in _DESTRUCTIVE_KW)


def _strip_prefix(path: str, base: str) -> str:
    """Return the breadcrumb of `path` after the `base` prefix (segment-aware)."""
    segs = [s.strip() for s in (path or "").split(">") if s.strip()]
    base_segs = [s.strip() for s in (base or "").split(">") if s.strip()]
    rest = segs[len(base_segs):] if segs[:len(base_segs)] == base_segs else segs
    return " > ".join(rest)


async def start_batch_run(run_id: str, base_path: str, case_ids: list, max_steps: int = 20) -> None:
    state = RunState(run_id)
    from core.device_scheduler import enqueue
    await enqueue(run_id, state, lambda: execute_batch_run(run_id, state, base_path, list(case_ids), max_steps))


async def execute_batch_run(run_id: str, state: "RunState", base_path: str,
                            case_ids: list, max_steps: int = 20) -> None:
    """Run many checks in ONE session: navigate to the base page once, then verify
    each leaf in turn (returning to base between, using the page stack), recording
    a per-leaf pass/fail. Destructive checks (logout, …) run last."""
    async def emit(msg: str) -> None:
        msg = state.redact(msg)
        logger.info("[batch:%s] %s", run_id, msg)
        await state.emit(msg)

    passed = failed = errored = skipped = 0
    recorder = RunRecorder(run_id)
    try:
        async with AsyncSessionLocal() as session:
            run_row = await session.get(TestRun, run_id)
            if not run_row:
                await emit("ERROR: run not found"); return
            device_id, provider, model = run_row.device_id, run_row.provider, run_row.model
            account_id = run_row.account_id
            suite_id = run_row.suite_id
            res = await session.execute(select(TestResult).where(TestResult.run_id == run_id))
            result_rows = {r.case_id: r for r in res.scalars().all()}
            cres = await session.execute(select(TestCase).where(TestCase.id.in_(case_ids)))
            case_map = {c.id: c for c in cres.scalars().all()}

        from core.test_accounts import context_for_run
        login_context = await context_for_run(run_id)
        if login_context:
            state.redact = login_context.redact
            await emit(f"测试账号: {login_context.name} · 环境: {login_context.environment}")
        project_kb_roots = await _project_kb_roots(suite_id)
        project_source_root = await _project_source_root(suite_id)
        project_kb_search_cmd = await _project_kb_search_cmd(suite_id)
        project_app_package = await _suite_app_package(suite_id, WebSocketDevice(device_id).platform)
        if project_app_package:
            await emit(f"目标应用: {project_app_package} · 平台: {WebSocketDevice(device_id).platform}")

        ordered = [cid for cid in case_ids if cid in case_map]
        safe = [cid for cid in ordered if not _is_destructive(f"{case_map[cid].path} {case_map[cid].expected}")]
        ordered = safe + [cid for cid in ordered if cid not in safe]

        conn = connected_devices.get(device_id)
        if conn is None or not conn.is_connected:
            await emit(f"ERROR: Device {device_id} is not connected"); return
        device = WebSocketDevice(device_id)
        settings = await load_settings(account_id)
        apply_aws_env(settings)
        api_key = resolve_api_key(settings, provider)
        api_base = resolve_api_base(settings, provider)
        v_provider, v_model, v_key, v_base = resolve_verifier(settings)
        fallbacks = build_fallback_chain(settings, provider, model)
        language = resolve_language(settings)
        if _missing_key(provider, api_key):
            await _mark_error(run_id, "当前账号未配置 API Key，请到设置页配置后再运行")
            await emit("ERROR: 当前账号未配置 API Key，请到设置页配置后再运行")
            return

        async with AsyncSessionLocal() as session:
            await session.execute(update(TestRun).where(TestRun.id == run_id).values(status="running"))
            await session.commit()

        await emit(f"Batch: {len(ordered)} checks on one session · base = {base_path} · model = {provider}/{model}")
        if len(ordered) > len(safe):
            await emit(f"  ⚠ {len(ordered) - len(safe)} destructive check(s) deferred to the end")
        if await recorder.start(device_id):
            await emit("Screen recording started (ADB)")

        async def log_cb(m: str) -> None:
            await emit(m)

        prev_path = ""
        for idx, cid in enumerate(ordered):
            case_row = case_map[cid]
            result_row = result_rows.get(cid)
            action = _strip_prefix(case_row.path, base_path)
            await emit(f"\n[{idx + 1}/{len(ordered)}] {action or case_row.path or '(verify on page)'} → {case_row.expected}")

            if base_path:
                # Folder batch: every leaf is reached from a single base page.
                goal = (
                    "You are batch-verifying checkpoints on a base page.\n"
                    f"BASE PAGE (you should be here; navigate to it first if you're not): {base_path}\n"
                    f"THIS CHECK: {action or 'verify directly on the base page — no navigation needed'}\n"
                    "When done: if this check took you into a sub-page, press back to return to the BASE PAGE. "
                    "Use 'Page stack' in [Device State] to tell whether you're on the base page and how many backs return to it."
                )
            else:
                # Whole-suite tree run: one continuous session over all cases.
                prev_line = f"The previous check targeted: {prev_path}\n" if prev_path else ""
                goal = (
                    f"This is check {idx + 1}/{len(ordered)} in a sequence run on the SAME app — do NOT restart the app.\n"
                    f"{prev_line}"
                    "You are very likely already on (or near) the target page from the previous check — "
                    "read 'Page stack' in [Device State] to see your REAL current position and navigate ONLY the remaining steps. "
                    "If a previous check left you deep in a sub-page, press back toward where this one needs you.\n"
                    f"NAVIGATE TO: {case_row.path}\n"
                    f"VERIFY: {case_row.expected}"
                )
            prev_path = case_row.path
            case_data = TestCaseData(
                path=goal, expected=case_row.expected,
                retrieval_query=f"{case_row.path} {case_row.expected or ''}".strip(),
            )

            # Cross-run memory: a soft reference from this case's best prior run
            # (starred, else last pass) + lessons distilled from past mistakes.
            task_kw = task_keyword_for(case_row.path)
            reference_examples, ref_msg = await load_reference_examples(cid)
            if ref_msg:
                await emit(ref_msg)
            lessons = await load_lessons(cid, case_row.suite_id, task_kw)
            if lessons:
                await emit(f"  📖 Loaded {len(lessons)} lessons from past runs")

            # Fresh agent (fresh memory) per check; the device keeps its state between checks.
            # Single-agent (no subgoal decomposition): each leaf is a small,
            # mostly-on-one-page check, so over-decomposing wastes steps + re-navigates.
            agent = TestCaseAgent(
                device=device, provider=provider, model=model, api_key=api_key, api_base=api_base,
                max_steps=max_steps, step_delay=1.0, log_callback=log_cb,
                verifier_provider=v_provider, verifier_model=v_model,
                verifier_api_key=v_key, verifier_api_base=v_base, fallbacks=fallbacks,
                allow_subagents=False,
                loop_task=case_row.loop_task,
                reference_examples=reference_examples or None,
                lessons_learned=lessons or None,
                project_kb_roots=project_kb_roots, source_root=project_source_root,
                kb_search_cmd=project_kb_search_cmd,
                target_package=project_app_package,
                login_context=login_context,
                language=language,
            )
            try:
                case_result = await agent.run(case_data)
            except asyncio.CancelledError:
                if result_row:
                    async with AsyncSessionLocal() as session:
                        await session.execute(update(TestResult).where(TestResult.id == result_row.id)
                            .values(status="error", reason="Run cancelled", finished_at=datetime.utcnow()))
                        await session.commit()
                raise
            except Exception as e:
                case_result = CaseResult(status="error", reason=str(e), steps=0)

            if result_row:
                async with AsyncSessionLocal() as session:
                    await session.execute(update(TestResult).where(TestResult.id == result_row.id).values(
                        status=case_result.status, reason=case_result.reason, steps=case_result.steps,
                        screenshot_b64=case_result.screenshot_b64, log=case_result.log,
                        finished_at=datetime.utcnow(),
                        action_history_json=json.dumps(case_result.action_history),
                        total_tokens=case_result.total_tokens,
                    ))
                    for sl in case_result.step_logs:
                        session.add(TestStepLog(
                            result_id=result_row.id, step=sl.step, thought=sl.thought,
                            action=sl.action, action_result=sl.action_result, screenshot_b64=sl.screenshot_b64,
                            prompt_tokens=sl.prompt_tokens, completion_tokens=sl.completion_tokens,
                            total_tokens=sl.total_tokens, perception_ms=sl.perception_ms,
                            llm_ms=sl.llm_ms, action_ms=sl.action_ms,
                            subgoal_index=sl.subgoal_index, subgoal_desc=sl.subgoal_desc or "",
                        ))
                    await session.commit()

            # Distil lessons from this leaf for future runs (best-effort).
            if result_row and case_result.steps >= 3:
                n_lessons = await extract_lessons(
                    result_id=result_row.id, run_id=run_id, case_id=cid,
                    suite_id=case_row.suite_id, task_keyword=task_kw,
                    provider=provider, model=model, api_key=api_key, api_base=api_base,
                )
                if n_lessons:
                    await emit(f"  📖 Extracted {n_lessons} lesson(s) for future runs")

            icon = {"pass": "✅", "fail": "❌", "error": "💥", "skip": "⏭"}.get(case_result.status, "?")
            await emit(f"  {icon} {case_result.status}: {case_result.reason}")
            if case_result.status == "pass": passed += 1
            elif case_result.status == "fail": failed += 1
            elif case_result.status == "skip": skipped += 1
            else: errored += 1

        async with AsyncSessionLocal() as session:
            await session.execute(update(TestRun).where(TestRun.id == run_id)
                .values(status="done", finished_at=datetime.utcnow()))
            await session.commit()
        await emit(f"\nBatch complete: {passed} passed, {failed} failed, {errored} error(s), {skipped} skipped")
        await _force_stop_app_after_run(device, project_app_package, emit)

    except asyncio.CancelledError:
        await emit("⛔ Run cancelled by user")
        async with AsyncSessionLocal() as session:
            await session.execute(update(TestRun).where(TestRun.id == run_id)
                .values(status="cancelled", finished_at=datetime.utcnow()))
            await session.commit()
    finally:
        await recorder.stop()
        from core.test_accounts import release_run_account
        await release_run_account(run_id)
        from core.device_scheduler import jobs
        if run_id not in jobs:
            await state.finish()
            active_runs.pop(run_id, None)


async def resolved_nodes_for_suite(session, suite_id: str) -> list:
    """Load a suite's StepNode rows and expand any live-link nodes (resolve_links)."""
    rows = (await session.execute(
        select(StepNode).where(StepNode.suite_id == suite_id)
    )).scalars().all()
    suite_rows = [
        NodeRow(id=r.id, parent_id=r.parent_id, action=r.action,
                expected=r.expected or "", order=r.order, reversible=r.reversible,
                ref_id=r.ref_id or "")
        for r in rows
    ]
    if not any(r.ref_id for r in suite_rows):
        return suite_rows
    all_rows = (await session.execute(select(StepNode))).scalars().all()
    all_by_id = {
        a.id: NodeRow(id=a.id, parent_id=a.parent_id, action=a.action,
                      expected=a.expected or "", order=a.order, reversible=a.reversible)
        for a in all_rows
    }
    return resolve_links(suite_rows, all_by_id)


async def node_targets_for_suite(session, suite_id: str, only_node_id: str = None) -> list:
    """Return RunTargets for a suite's step-tree (live links resolved).

    Default: one target per leaf, in DFS order. When `only_node_id` is given,
    return a single target = the root→that-node chain (the node may be a
    non-leaf), or [] if the node is unknown.
    """
    node_rows = await resolved_nodes_for_suite(session, suite_id)
    if only_node_id is not None:
        chain = chain_to_node(node_rows, only_node_id)
        return [RunTarget(node_id=only_node_id, chain=chain)] if chain else []
    return dfs_run_targets(node_rows)


async def start_tree_run(run_id: str, max_steps: int = 20, only_node_id: str = None) -> None:
    state = RunState(run_id)
    from core.device_scheduler import enqueue
    await enqueue(run_id, state, lambda: execute_tree_run(run_id, state, max_steps, only_node_id))


async def execute_tree_run(run_id: str, state: "RunState", max_steps: int = 20,
                           only_node_id: str = None) -> None:
    """Run a suite's step-tree as a DFS over leaf targets in one session.

    Leaves come out in DFS order so a shared prefix is navigated once; the agent
    is told to use the page stack to backtrack to the divergence point between
    consecutive leaves (Phase 1: back-navigation only). When `only_node_id` is
    set, a single node's root→node chain is run instead of the whole tree.
    """
    async def emit(msg: str) -> None:
        msg = state.redact(msg)
        logger.info("[tree:%s] %s", run_id, msg)
        await state.emit(msg)

    passed = failed = errored = skipped = 0
    recorder = RunRecorder(run_id)
    try:
        async with AsyncSessionLocal() as session:
            run_row = await session.get(TestRun, run_id)
            if not run_row:
                await emit("ERROR: run not found"); return
            device_id, provider, model = run_row.device_id, run_row.provider, run_row.model
            account_id = run_row.account_id
            suite_id = run_row.suite_id
            targets = await node_targets_for_suite(session, suite_id, only_node_id)
            # Resolved node rows (links expanded, incl. reversible) for backtrack planning.
            node_rows = await resolved_nodes_for_suite(session, suite_id)
            res = await session.execute(select(TestResult).where(TestResult.run_id == run_id))
            result_rows = {r.case_id: r for r in res.scalars().all()}

        from core.test_accounts import context_for_run
        login_context = await context_for_run(run_id)
        if login_context:
            state.redact = login_context.redact
            await emit(f"测试账号: {login_context.name} · 环境: {login_context.environment}")
        project_kb_roots = await _project_kb_roots(suite_id)
        project_source_root = await _project_source_root(suite_id)
        project_kb_search_cmd = await _project_kb_search_cmd(suite_id)
        project_app_package = await _suite_app_package(suite_id, WebSocketDevice(device_id).platform)
        if project_app_package:
            await emit(f"目标应用: {project_app_package} · 平台: {WebSocketDevice(device_id).platform}")

        conn = connected_devices.get(device_id)
        if conn is None or not conn.is_connected:
            await emit(f"ERROR: Device {device_id} is not connected"); return
        device = WebSocketDevice(device_id)
        settings = await load_settings(account_id)
        apply_aws_env(settings)
        api_key = resolve_api_key(settings, provider)
        api_base = resolve_api_base(settings, provider)
        v_provider, v_model, v_key, v_base = resolve_verifier(settings)
        fallbacks = build_fallback_chain(settings, provider, model)
        language = resolve_language(settings)
        if _missing_key(provider, api_key):
            await _mark_error(run_id, "当前账号未配置 API Key，请到设置页配置后再运行")
            await emit("ERROR: 当前账号未配置 API Key，请到设置页配置后再运行")
            return

        async with AsyncSessionLocal() as session:
            await session.execute(update(TestRun).where(TestRun.id == run_id).values(status="running"))
            await session.commit()

        await emit(f"Tree run: {len(targets)} leaf case(s) · DFS · model = {provider}/{model}")
        if await recorder.start(device_id):
            await emit("Screen recording started (ADB)")

        async def log_cb(m: str) -> None:
            await emit(m)

        prev_node_id = None
        for idx, target in enumerate(targets):
            flat = flatten_chain(target.chain)
            plan = backtrack_plan(node_rows, prev_node_id, target.node_id)
            await emit(f"\n[{idx + 1}/{len(targets)}] {flat.path} → {flat.expected or '(执行成功即通过)'}  · {plan.kind}")

            def _num(items, start=1):
                out = []
                for i, it in enumerate(items, start):
                    s = f"{i}. {it.action}"
                    if it.expected:
                        s += f"（期望: {it.expected}）"
                    out.append(s)
                return "\n".join(out)

            # For a 'back' transition, only the steps AFTER the divergence point
            # (LCA) are new — instruct the agent to return there and perform just
            # those, and FORBID passing on a stale screen left by the previous case.
            lca_idx = -1
            if plan.kind == "back" and plan.to_node_id:
                for i, it in enumerate(target.chain):
                    if it.node_id == plan.to_node_id:
                        lca_idx = i
                        break

            if plan.kind == "back" and lca_idx >= 0 and lca_idx < len(target.chain) - 1:
                lca_action = target.chain[lca_idx].action
                suffix = target.chain[lca_idx + 1:]
                goal = (
                    f"This is case {idx + 1}/{len(targets)} in a DFS run — continue on the SAME app, do NOT restart it.\n"
                    f"上一条用例和本条在「{lca_action}」处分叉。请用 [Device State] 的 Page stack 判断位置，"
                    f"按返回键回到「{lca_action}」这一步所在的页面，然后从那里开始，按顺序执行下面这些**剩余步骤**：\n"
                    f"{_num(suffix)}\n"
                    f"⚠ 必须真正执行上面的操作。即使当前画面看起来已经满足期望（上一条用例可能把你留在了那个页面），"
                    f"也不能直接判通过——本条用例测试的是**不同的操作路径**，你必须实际走一遍这些步骤。\n"
                    f"最终验证：{flat.expected or '完成上述步骤即视为通过'}"
                )
            else:
                if plan.kind == "replay":
                    head = (
                        "⚠ 上一条用例提交了不可回退的操作（如一次性选择/提交），按返回键无法恢复干净状态。"
                        "请从 App 首页/启动态重新开始，完整执行本条用例的全部步骤，不要假设当前位置。\n"
                    )
                else:  # fresh
                    head = "这是第一条用例。从 App 首页/启动态开始，按顺序执行全部步骤。\n"
                goal = (
                    f"This is case {idx + 1}/{len(targets)} in a DFS run.\n"
                    f"{head}"
                    f"按顺序完成以下步骤：\n{_num(target.chain)}\n"
                    f"最终验证：{flat.expected or '完成上述全部步骤即视为通过（无额外结果验证）'}"
                )
            prev_node_id = target.node_id
            case_data = TestCaseData(
                path=goal, expected=flat.expected, steps=[],
                retrieval_query=f"{flat.path} {flat.expected or ''}".strip(),
            )

            result_row = result_rows.get(target.node_id)
            # Mark this case running so the live result tree shows where we are
            # (TestResult otherwise stays 'pending' until the case finishes).
            if result_row:
                async with AsyncSessionLocal() as session:
                    await session.execute(update(TestResult).where(TestResult.id == result_row.id)
                        .values(status="running", started_at=datetime.utcnow()))
                    await session.commit()
            agent = TestCaseAgent(
                device=device, provider=provider, model=model, api_key=api_key, api_base=api_base,
                max_steps=max_steps, step_delay=1.0, log_callback=log_cb,
                verifier_provider=v_provider, verifier_model=v_model,
                verifier_api_key=v_key, verifier_api_base=v_base, fallbacks=fallbacks,
                allow_subagents=False,
                project_kb_roots=project_kb_roots, source_root=project_source_root,
                kb_search_cmd=project_kb_search_cmd,
                target_package=project_app_package,
                login_context=login_context,
                language=language,
            )
            try:
                case_result = await agent.run(case_data)
            except asyncio.CancelledError:
                if result_row:
                    async with AsyncSessionLocal() as session:
                        await session.execute(update(TestResult).where(TestResult.id == result_row.id)
                            .values(status="error", reason="Run cancelled", finished_at=datetime.utcnow()))
                        await session.commit()
                raise
            except Exception as e:
                case_result = CaseResult(status="error", reason=str(e), steps=0)

            if result_row:
                async with AsyncSessionLocal() as session:
                    await session.execute(update(TestResult).where(TestResult.id == result_row.id).values(
                        status=case_result.status, reason=case_result.reason, steps=case_result.steps,
                        screenshot_b64=case_result.screenshot_b64, log=case_result.log,
                        finished_at=datetime.utcnow(),
                        action_history_json=json.dumps(case_result.action_history),
                        total_tokens=case_result.total_tokens,
                    ))
                    for sl in case_result.step_logs:
                        session.add(TestStepLog(
                            result_id=result_row.id, step=sl.step, thought=sl.thought,
                            action=sl.action, action_result=sl.action_result, screenshot_b64=sl.screenshot_b64,
                            prompt_tokens=sl.prompt_tokens, completion_tokens=sl.completion_tokens,
                            total_tokens=sl.total_tokens, perception_ms=sl.perception_ms,
                            llm_ms=sl.llm_ms, action_ms=sl.action_ms,
                            subgoal_index=sl.subgoal_index, subgoal_desc=sl.subgoal_desc or "",
                        ))
                    await session.commit()

            icon = {"pass": "✅", "fail": "❌", "error": "💥", "skip": "⏭"}.get(case_result.status, "?")
            await emit(f"  {icon} {case_result.status}: {case_result.reason}")
            if case_result.status == "pass": passed += 1
            elif case_result.status == "fail": failed += 1
            elif case_result.status == "skip": skipped += 1
            else: errored += 1

        async with AsyncSessionLocal() as session:
            await session.execute(update(TestRun).where(TestRun.id == run_id)
                .values(status="done", finished_at=datetime.utcnow()))
            await session.commit()
        await emit(f"\nTree run complete: {passed} passed, {failed} failed, {errored} error(s), {skipped} skipped")
        await _force_stop_app_after_run(device, project_app_package, emit)

    except asyncio.CancelledError:
        await emit("⛔ Run cancelled by user")
        async with AsyncSessionLocal() as session:
            await session.execute(update(TestRun).where(TestRun.id == run_id)
                .values(status="cancelled", finished_at=datetime.utcnow()))
            await session.commit()
    finally:
        await recorder.stop()
        from core.test_accounts import release_run_account
        await release_run_account(run_id)
        from core.device_scheduler import jobs
        if run_id not in jobs:
            await state.finish()
            active_runs.pop(run_id, None)


async def cancel_run(run_id: str) -> bool:
    """Cancel an active run. Returns True if the run was found and cancelled."""
    from core.device_scheduler import cancel
    return await cancel(run_id)


async def run_log_stream(run_id: str) -> AsyncGenerator[str, None]:
    """SSE generator for an active run (history replay + live follow)."""
    state = active_runs.get(run_id)
    if state is None:
        yield "data: Run not found or not active\n\n"
        return
    async for chunk in state.stream():
        yield chunk


# ── Background task ───────────────────────────────────────────────────────────

async def _replay_step(device: WebSocketDevice, step: dict, emit) -> str:
    """Execute one recorded step verbatim. Returns a short description."""
    kind = step.get("kind")
    if kind == "tap":
        # Prefer element matching (id > text > class) so a minor UI shift doesn't
        # break the run; fall back to recorded coordinates last.
        if step.get("resourceId"):
            try:
                return await device.tap_element_by_id(step["resourceId"])
            except Exception:
                pass
        if step.get("text"):
            try:
                return await device.tap_element_by_text(step["text"])
            except Exception:
                pass
        if step.get("className"):
            try:
                return await device.tap_element_by_class(step["className"])
            except Exception:
                pass
        if step.get("x") is not None and step.get("y") is not None:
            await device.tap(step["x"], step["y"])
            return f"tap ({step['x']},{step['y']})"
        return "tap (no target)"
    if kind == "tap_element":
        await device.tap_element(step.get("index", 0))
        return f"tap_element[{step.get('index')}]"
    if kind == "scroll":
        # Older Portal builds may not record a direction (TYPE_VIEW_SCROLLED
        # doesn't expose it directly). Fall back to "down" rather than crash.
        direction = step.get("direction") or "down"
        await device.scroll(direction)
        return f"scroll {direction}"
    if kind == "swipe":
        await device.swipe(step.get("x1"), step.get("y1"), step.get("x2"), step.get("y2"),
                           step.get("duration_ms", 500))
        return "swipe"
    if kind == "input_test_credential":
        raise ValueError("凭据填写不能回放，请使用 AI 模式并选择测试账号")
    if kind == "input_text":
        await device.input_text(step.get("text", ""), step.get("clear", False))
        return f"input {str(step.get('text',''))[:20]}"
    if kind == "global_action":
        await device.global_action(step.get("action", ""))
        return f"global {step.get('action')}"
    if kind == "press_key":
        await device.press_key(step.get("key", ""))
        return f"key {step.get('key')}"
    if kind == "start_app":
        await device.start_app(step.get("package", ""))
        return f"start {step.get('package')}"
    return f"unknown {kind}"


async def execute_replay_run(
    run_id: str,
    state: RunState,
    max_steps: int = 20,
    step_delay: float = 1.0,
    max_retries: int = 0,
) -> None:
    """Deterministic replay: execute recorded actions verbatim (coordinates +
    timing) with NO per-step AI decisions, then verify ONCE at the end."""
    async def emit(msg: str) -> None:
        msg = state.redact(msg)
        logger.info("[replay:%s] %s", run_id, msg)
        await state.emit(msg)

    recorder = RunRecorder(run_id)
    try:
        async with AsyncSessionLocal() as session:
            run_row = await session.get(TestRun, run_id)
            if not run_row:
                await emit(f"ERROR: run {run_id} not found")
                return
            device_id = run_row.device_id
            provider = run_row.provider
            model = run_row.model
            account_id = run_row.account_id
            cases = (await session.execute(
                select(TestCase).where(TestCase.suite_id == run_row.suite_id).order_by(TestCase.order)
            )).scalars().all()
            result_rows = {r.case_id: r for r in (await session.execute(
                select(TestResult).where(TestResult.run_id == run_id)
            )).scalars().all()}

        conn = connected_devices.get(device_id)
        if conn is None or not conn.is_connected:
            async with AsyncSessionLocal() as session:
                await session.execute(update(TestRun).where(TestRun.id == run_id)
                                     .values(status="error", finished_at=datetime.utcnow()))
                await session.commit()
            await emit(f"ERROR: Device {device_id} is not connected")
            return

        device = WebSocketDevice(device_id)
        settings = await load_settings(account_id)
        apply_aws_env(settings)
        api_key = resolve_api_key(settings, provider)
        api_base = resolve_api_base(settings, provider)
        v_provider, v_model, v_key, v_base = resolve_verifier(settings)
        fallbacks = build_fallback_chain(settings, provider, model)
        language = resolve_language(settings)
        if _missing_key(provider, api_key):
            await _mark_error(run_id, "当前账号未配置 API Key，请到设置页配置后再运行")
            await emit("ERROR: 当前账号未配置 API Key，请到设置页配置后再运行")
            return

        async with AsyncSessionLocal() as session:
            await session.execute(update(TestRun).where(TestRun.id == run_id).values(status="running"))
            await session.commit()

        await emit(f"Replay run: {len(cases)} case(s) · device={device_id} · deterministic (no per-step AI)")
        if await recorder.start(device_id):
            await emit("Screen recording started (ADB)")

        verifier = LLMVerifier(v_provider, v_model, v_key, v_base, fallbacks=fallbacks, language=language)

        passed = failed = errored = skipped = 0
        for case in cases:
            result_row = result_rows.get(case.id)
            if result_row:
                async with AsyncSessionLocal() as session:
                    await session.execute(update(TestResult).where(TestResult.id == result_row.id)
                                         .values(status="running", started_at=datetime.utcnow()))
                    await session.commit()

            await emit(f"\n[{case.path[:60]}]")
            try:
                replay = json.loads(case.replay_steps or "[]")
                if not replay:
                    case_result = CaseResult(
                        status="error", steps=0,
                        reason="No replay steps recorded — record first, or run in AI mode",
                    )
                else:
                    action_history: list = []
                    for i, step in enumerate(replay):
                        delay_ms = int(step.get("delay_ms", 0))
                        if delay_ms > 0:
                            await asyncio.sleep(delay_ms / 1000)
                        desc = await _replay_step(device, step, emit)
                        action_history.append(f"Step {i + 1}: {desc}")
                        await emit(f"  [{i + 1}/{len(replay)}] {desc}")

                    await emit("  Verifying expected result (single AI check)…")
                    confirmed, reason, shot, _gap = await verifier.verify(
                        device, case.expected, log=emit,
                        action_history=action_history, agent_reason="replay",
                    )
                    status = "pass" if confirmed else "fail"
                    case_result = CaseResult(
                        status=status, reason=reason, steps=len(replay), screenshot_b64=shot,
                    )
            except asyncio.CancelledError:
                if result_row:
                    async with AsyncSessionLocal() as session:
                        await session.execute(update(TestResult).where(TestResult.id == result_row.id)
                                             .values(status="error", reason="Run cancelled", finished_at=datetime.utcnow()))
                        await session.commit()
                raise
            except Exception as e:
                case_result = CaseResult(status="error", reason=str(e), steps=0)

            if result_row:
                async with AsyncSessionLocal() as session:
                    await session.execute(update(TestResult).where(TestResult.id == result_row.id).values(
                        status=case_result.status, reason=case_result.reason, steps=case_result.steps,
                        screenshot_b64=case_result.screenshot_b64, finished_at=datetime.utcnow(),
                    ))
                    await session.commit()

            if case_result.status == "pass":
                passed += 1
            elif case_result.status == "fail":
                failed += 1
            elif case_result.status == "error":
                errored += 1
            else:
                skipped += 1
            await emit(f"  → {case_result.status}")

        async with AsyncSessionLocal() as session:
            await session.execute(update(TestRun).where(TestRun.id == run_id)
                                 .values(status="done", finished_at=datetime.utcnow()))
            await session.commit()
        await emit(f"\nReplay complete: {passed} passed, {failed} failed, {errored} error(s), {skipped} skipped")

    except asyncio.CancelledError:
        await emit("⛔ Replay cancelled")
        async with AsyncSessionLocal() as session:
            await session.execute(update(TestRun).where(TestRun.id == run_id)
                                 .values(status="cancelled", finished_at=datetime.utcnow()))
            await session.commit()
    finally:
        await recorder.stop()
        from core.test_accounts import release_run_account
        await release_run_account(run_id)
        from core.device_scheduler import jobs
        if run_id not in jobs:
            await state.finish()
            active_runs.pop(run_id, None)


async def execute_run(
    run_id: str,
    state: RunState,
    max_steps: int = 20,
    step_delay: float = 1.0,
    max_retries: int = 0,
) -> None:
    """Main run loop. Called as an asyncio task via start_run()."""

    async def emit(msg: str) -> None:
        msg = state.redact(msg)
        logger.info("[run:%s] %s", run_id, msg)
        await state.emit(msg)

    recorder = RunRecorder(run_id)
    try:
        async with AsyncSessionLocal() as session:
            run_row = await session.get(TestRun, run_id)
            if not run_row:
                await emit(f"ERROR: run {run_id} not found")
                return

            device_id = run_row.device_id
            provider = run_row.provider
            model = run_row.model
            account_id = run_row.account_id

            result = await session.execute(
                select(TestCase)
                .where(TestCase.suite_id == run_row.suite_id)
                .order_by(TestCase.order)
            )
            cases = result.scalars().all()

            res_result = await session.execute(
                select(TestResult).where(TestResult.run_id == run_id)
            )
            result_rows = {r.case_id: r for r in res_result.scalars().all()}

        # Check device is connected
        conn = connected_devices.get(device_id)
        if conn is None or not conn.is_connected:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(TestRun).where(TestRun.id == run_id)
                    .values(status="error", finished_at=datetime.utcnow())
                )
                await session.commit()
            await emit(f"ERROR: Device {device_id} is not connected")
            return

        device = WebSocketDevice(device_id)
        settings = await load_settings(account_id)
        apply_aws_env(settings)  # export AWS creds so litellm/boto3 can reach Bedrock
        api_key = resolve_api_key(settings, provider)
        api_base = resolve_api_base(settings, provider)
        v_provider, v_model, v_key, v_base = resolve_verifier(settings)
        fallbacks = build_fallback_chain(settings, provider, model)
        language = resolve_language(settings)
        if _missing_key(provider, api_key):
            await _mark_error(run_id, "当前账号未配置 API Key，请到设置页配置后再运行")
            await emit("ERROR: 当前账号未配置 API Key，请到设置页配置后再运行")
            return
        if fallbacks:
            await emit(f"Fallback models: {', '.join(t.label() for t in fallbacks)}")
        from core.test_accounts import context_for_run
        login_context = await context_for_run(run_id)
        if login_context:
            state.redact = login_context.redact
            await emit(f"测试账号: {login_context.name} · 环境: {login_context.environment}")
        project_kb_roots = await _project_kb_roots(run_row.suite_id)
        project_source_root = await _project_source_root(run_row.suite_id)
        project_kb_search_cmd = await _project_kb_search_cmd(run_row.suite_id)
        project_app_package = await _suite_app_package(run_row.suite_id, device.platform)
        if project_app_package:
            await emit(f"目标应用: {project_app_package} · 平台: {device.platform}")

        # Mark run as running
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(TestRun).where(TestRun.id == run_id).values(status="running")
            )
            await session.commit()

        await emit(f"Run started: {len(cases)} test cases, device={device_id}, model={provider}/{model}")
        if await recorder.start(device_id):
            await emit("Screen recording started (ADB)")

        passed = failed = errored = skipped = 0

        def _parse_checkpoints(raw: str) -> list[Step]:
            """Decode the JSON checkpoints column into a list of Step.

            Tolerant: malformed entries are skipped so a typo in one case
            doesn't break the whole run.
            """
            if not raw:
                return []
            try:
                items = json.loads(raw)
            except Exception:
                return []
            if not isinstance(items, list):
                return []
            out: list[Step] = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                action = str(it.get("action", "")).strip()
                expected_ = str(it.get("expected", "")).strip()
                if action and expected_:
                    out.append(Step(action=action, expected=expected_))
            return out

        def _expand_params(
            path: str, expected: str, params_json: str, steps: list[Step],
        ) -> list[TestCaseData]:
            """Expand a parameterized case into multiple TestCaseData instances.

            Substitution applies to path, expected, and every step's
            action/expected text so checkpoints can use the same {{var}}
            placeholders as the legacy fields.
            """
            base = TestCaseData(path=path, expected=expected, steps=list(steps))
            if not params_json:
                return [base]
            try:
                param_sets = json.loads(params_json)
                if not isinstance(param_sets, list) or len(param_sets) == 0:
                    return [base]
            except Exception:
                return [base]
            expanded: list[TestCaseData] = []
            for ps in param_sets:
                if not isinstance(ps, dict):
                    continue
                p = path
                e = expected
                substituted_steps: list[Step] = []
                for s in steps:
                    sa, se = s.action, s.expected
                    for k, v in ps.items():
                        sa = sa.replace(f"{{{{{k}}}}}", str(v))
                        se = se.replace(f"{{{{{k}}}}}", str(v))
                    substituted_steps.append(Step(action=sa, expected=se))
                for k, v in ps.items():
                    p = p.replace(f"{{{{{k}}}}}", str(v))
                    e = e.replace(f"{{{{{k}}}}}", str(v))
                expanded.append(TestCaseData(path=p, expected=e, steps=substituted_steps))
            return expanded or [base]

        # Expand parameterized cases into flat list
        expanded_cases = []
        for case_row in cases:
            row_steps = _parse_checkpoints(getattr(case_row, "checkpoints", "") or "")
            variants = _expand_params(
                case_row.path, case_row.expected,
                case_row.parameters or "", row_steps,
            )
            for v in variants:
                expanded_cases.append((case_row, v))

        for idx, (case_row, case_data) in enumerate(expanded_cases):
            result_row = result_rows.get(case_row.id)

            cp_tag = f" | {len(case_data.steps)} checkpoints" if case_data.steps else ""
            await emit(f"\n[{idx+1}/{len(expanded_cases)}] {case_data.path} | {case_data.expected}{cp_tag}")

            if result_row:
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(TestResult).where(TestResult.id == result_row.id)
                        .values(status="running", started_at=datetime.utcnow())
                    )
                    await session.commit()

            async def log_cb(msg: str) -> None:
                await emit(f"  {msg}")

            # Cross-run memory: a soft reference from this case's best prior run
            # (starred, else last pass — "success auto-memory") + lessons.
            reference_examples, ref_msg = await load_reference_examples(case_row.id)
            if ref_msg:
                await emit(ref_msg)

            _task_kw = task_keyword_for(case_row.path)
            lessons = await load_lessons(case_row.id, case_row.suite_id, _task_kw)
            if lessons:
                await emit(f"  📖 Loaded {len(lessons)} lessons from past runs")

            agent = TestCaseAgent(
                device=device,
                provider=provider,
                model=model,
                api_key=api_key,
                api_base=api_base,
                max_steps=max_steps,
                step_delay=step_delay,
                log_callback=log_cb,
                verifier_provider=v_provider,
                verifier_model=v_model,
                verifier_api_key=v_key,
                verifier_api_base=v_base,
                loop_task=case_row.loop_task,
                reference_examples=reference_examples or None,
                lessons_learned=lessons or None,
                fallbacks=fallbacks,
                project_kb_roots=project_kb_roots, source_root=project_source_root,
                kb_search_cmd=project_kb_search_cmd,
                target_package=project_app_package,
                login_context=login_context,
                language=language,
            )

            case_result: Optional[CaseResult] = None
            for attempt in range(1 + max_retries):
                try:
                    case_result = await agent.run(case_data)
                except asyncio.CancelledError:
                    if result_row:
                        async with AsyncSessionLocal() as session:
                            await session.execute(
                                update(TestResult).where(TestResult.id == result_row.id)
                                .values(status="error", reason="Run cancelled", finished_at=datetime.utcnow())
                            )
                            await session.commit()
                    raise
                except Exception as e:
                    case_result = CaseResult(status="error", reason=str(e), steps=0)

                # Retry on fail/error if retries remain
                if case_result.status in ("fail", "error") and attempt < max_retries:
                    await emit(f"  ↩ Retry {attempt + 1}/{max_retries} — resetting to home screen…")
                    try:
                        await device.global_action("home")
                        await asyncio.sleep(2.0)
                    except Exception:
                        pass
                    # Recreate agent with fresh memory for the retry
                    agent = TestCaseAgent(
                        device=device, provider=provider, model=model,
                        api_key=api_key, api_base=api_base,
                        max_steps=max_steps, step_delay=step_delay,
                        log_callback=log_cb,
                        verifier_provider=v_provider, verifier_model=v_model,
                        verifier_api_key=v_key, verifier_api_base=v_base,
                        loop_task=case_row.loop_task,
                        reference_examples=reference_examples or None,
                        fallbacks=fallbacks,
                        project_kb_roots=project_kb_roots, source_root=project_source_root,
                        kb_search_cmd=project_kb_search_cmd,
                        target_package=project_app_package,
                        login_context=login_context,
                        language=language,
                    )
                    continue
                break  # pass or no retries left

            if result_row:
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(TestResult).where(TestResult.id == result_row.id)
                        .values(
                            status=case_result.status,
                            reason=case_result.reason,
                            steps=case_result.steps,
                            screenshot_b64=case_result.screenshot_b64,
                            log=case_result.log,
                            finished_at=datetime.utcnow(),
                            action_history_json=json.dumps(case_result.action_history),
                            total_tokens=case_result.total_tokens,
                        )
                    )
                    # Persist per-step replay data
                    for sl in case_result.step_logs:
                        session.add(TestStepLog(
                            result_id=result_row.id,
                            step=sl.step,
                            thought=sl.thought,
                            action=sl.action,
                            action_result=sl.action_result,
                            screenshot_b64=sl.screenshot_b64,
                            prompt_tokens=sl.prompt_tokens,
                            completion_tokens=sl.completion_tokens,
                            total_tokens=sl.total_tokens,
                            perception_ms=sl.perception_ms,
                            llm_ms=sl.llm_ms,
                            action_ms=sl.action_ms,
                            subgoal_index=sl.subgoal_index,
                            subgoal_desc=sl.subgoal_desc or "",
                        ))
                    await session.commit()

            # Extract lessons from mistakes (async, best-effort)
            if result_row and case_result.steps >= 3:
                n_lessons = await extract_lessons(
                    result_id=result_row.id, run_id=run_id, case_id=case_row.id,
                    suite_id=case_row.suite_id, task_keyword=_task_kw,
                    provider=provider, model=model, api_key=api_key, api_base=api_base,
                )
                if n_lessons:
                    await emit(f"  📖 Extracted {n_lessons} lesson(s) for future runs")

            status_icon = {"pass": "✅", "fail": "❌", "error": "💥", "skip": "⏭"}.get(
                case_result.status, "?"
            )
            await emit(f"  {status_icon} {case_result.status}: {case_result.reason}")

            if case_result.status == "pass":
                passed += 1
            elif case_result.status == "fail":
                failed += 1
            elif case_result.status == "skip":
                skipped += 1
            else:
                errored += 1

        # Finalize
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(TestRun).where(TestRun.id == run_id)
                .values(status="done", finished_at=datetime.utcnow())
            )
            await session.commit()

        await emit(
            f"\nRun complete: {passed} passed, {failed} failed, "
            f"{errored} error(s), {skipped} skipped"
        )
        await _force_stop_app_after_run(device, project_app_package, emit)

        # Send webhook notification
        try:
            from core.webhook import send_run_notification
            suite_name = ""
            run_webhook_url = ""
            async with AsyncSessionLocal() as session:
                from db.models import TestSuite
                run_row2 = await session.get(TestRun, run_id)
                if run_row2:
                    suite_obj = await session.get(TestSuite, run_row2.suite_id)
                    suite_name = suite_obj.name if suite_obj else ""
                    run_webhook_url = run_row2.webhook_url or ""
            await send_run_notification(
                run_id=run_id, suite_name=suite_name,
                passed=passed, failed=failed, errored=errored,
                total=len(cases), provider=provider, model=model,
                webhook_url=run_webhook_url,
            )
        except Exception as wh_err:
            logger.warning("Webhook notification failed: %s", wh_err)

    except asyncio.CancelledError:
        await emit("⛔ Run cancelled by user")
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(TestRun).where(TestRun.id == run_id)
                .values(status="cancelled", finished_at=datetime.utcnow())
            )
            await session.commit()

    finally:
        await recorder.stop()
        from core.test_accounts import release_run_account
        await release_run_account(run_id)
        from core.device_scheduler import jobs
        if run_id not in jobs:
            await state.finish()
            active_runs.pop(run_id, None)
