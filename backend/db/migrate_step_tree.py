"""One-time migration: legacy flat TestCase rows -> StepNode tree.

Idempotent per suite: a suite that already has StepNode rows is skipped.
"""
from __future__ import annotations

import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.step_tree import BuiltNode, LegacyCase, build_tree_from_cases
from db.models import StepNode, TestCase, TestSuite


def _checkpoints(raw: str) -> list[tuple]:
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except Exception:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict):
            a = str(it.get("action", "")).strip()
            e = str(it.get("expected", "")).strip()
            if a and e:
                out.append((a, e))
    return out


async def migrate_suite_to_step_tree(session: AsyncSession, suite_id: str) -> int:
    """Materialize a suite's flat cases into StepNode rows. Returns nodes created.

    No-op (returns 0) if the suite already has StepNode rows.
    """
    suite = await session.get(TestSuite, suite_id)
    cases = (await session.execute(
        select(TestCase).where(TestCase.suite_id == suite_id).order_by(TestCase.order)
    )).scalars().all()

    # A recorder suite stores its executable actions in replay_steps. Its case
    # title is metadata, not a step-tree action; never synthesize tree nodes.
    if suite and suite.source_format == "recorded":
        existing_nodes = (await session.execute(
            select(StepNode).where(StepNode.suite_id == suite_id)
        )).scalars().all()
        # Clean up only the exact one-node legacy artifact created by the old
        # flat-case migration. Preserve any nontrivial, user-authored tree.
        if len(existing_nodes) == 1 and len(cases) == 1:
            node, case = existing_nodes[0], cases[0]
            if node.parent_id is None and node.action == case.path and (node.expected or "") == (case.expected or ""):
                await session.delete(node)
                await session.commit()
        return 0

    existing = (await session.execute(
        select(StepNode.id).where(StepNode.suite_id == suite_id).limit(1)
    )).first()
    if existing is not None:
        return 0
    if not cases:
        return 0

    legacy = [
        LegacyCase(
            path=c.path, expected=c.expected or "", case_id=c.id,
            loop_task=bool(getattr(c, "loop_task", False)),
            checkpoints=_checkpoints(c.checkpoints or ""),
        )
        for c in cases
    ]
    roots = build_tree_from_cases(legacy)

    created = 0
    async def _persist(node: BuiltNode, parent_id: str | None) -> None:
        nonlocal created
        row = StepNode(
            suite_id=suite_id, parent_id=parent_id, action=node.action,
            expected=node.expected, order=node.order, loop_task=node.loop_task,
        )
        session.add(row)
        await session.flush()  # assign row.id
        created += 1
        for child in node.children:
            await _persist(child, row.id)

    for r in roots:
        await _persist(r, None)

    await session.commit()
    return created
