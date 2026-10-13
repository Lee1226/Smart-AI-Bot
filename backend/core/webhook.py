"""
Webhook notification — push run results to external services.

Supported types:
  - feishu: Feishu/Lark bot webhook (card message)
  - dingtalk: DingTalk bot webhook (markdown message)
  - slack: Slack incoming webhook (blocks)
  - custom: POST JSON body to any URL
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from core.settings_store import load_settings, resolve_webhook
from db.database import AsyncSessionLocal
from db.models import TestRun

logger = logging.getLogger(__name__)


async def _load_webhook_config(run_id: str) -> tuple[str, str]:
    """Return (webhook_url, webhook_type) from the run's owning account settings."""
    async with AsyncSessionLocal() as db:
        run = await db.get(TestRun, run_id)
        if run is None or not run.account_id:
            return "", ""
        settings = await load_settings(run.account_id)
    return resolve_webhook(settings)


def _build_feishu_body(title: str, text: str, link: str) -> dict:
    """Feishu/Lark bot card message."""
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": [
                {"tag": "markdown", "content": text},
                {
                    "tag": "action",
                    "actions": [{
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看详情"},
                        "url": link,
                        "type": "primary",
                    }],
                },
            ],
        },
    }


def _build_dingtalk_body(title: str, text: str, link: str) -> dict:
    """DingTalk bot markdown message."""
    return {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": f"## {title}\n\n{text}\n\n[查看详情]({link})",
        },
    }


def _build_slack_body(title: str, text: str, link: str) -> dict:
    """Slack incoming webhook blocks."""
    return {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": title},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            },
            {
                "type": "actions",
                "elements": [{
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View Details"},
                    "url": link,
                }],
            },
        ],
    }


async def send_run_notification(
    run_id: str,
    suite_name: str,
    passed: int,
    failed: int,
    errored: int,
    total: int,
    provider: str,
    model: str,
    base_url: str = "",
    webhook_url: str = "",
) -> None:
    """Send a webhook notification for a completed run.

    Silently returns if no webhook is configured. A per-run *webhook_url* overrides
    the globally configured URL (settings.json) — used by CLI / API-triggered runs
    that want the result delivered to their own callback.
    """
    url, wh_type = await _load_webhook_config(run_id)
    if webhook_url:
        url = webhook_url
        # Per-run webhook defaults to the raw "custom" structured payload.
        if not wh_type:
            wh_type = "custom"
    if not url:
        return

    status_emoji = "✅" if failed == 0 and errored == 0 else "❌"
    title = f"{status_emoji} Test Run Complete: {suite_name}"
    text = (
        f"**Suite:** {suite_name}\n"
        f"**Model:** {provider}/{model}\n"
        f"**Results:** {passed} passed / {failed} failed / {errored} errored / {total} total\n"
        f"**Pass Rate:** {(passed / total * 100) if total > 0 else 0:.0f}%"
    )
    link = f"{base_url}/runs/{run_id}" if base_url else f"/runs/{run_id}"

    wh_type = wh_type.lower().strip()
    if wh_type == "feishu":
        body = _build_feishu_body(title, text, link)
    elif wh_type == "dingtalk":
        body = _build_dingtalk_body(title, text, link)
    elif wh_type == "slack":
        body = _build_slack_body(title, text, link)
    else:
        # Custom: send raw JSON
        body = {
            "title": title,
            "text": text,
            "link": link,
            "run_id": run_id,
            "suite_name": suite_name,
            "passed": passed,
            "failed": failed,
            "errored": errored,
            "total": total,
        }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=body)
            if resp.status_code >= 400:
                logger.warning("Webhook returned %d: %s", resp.status_code, resp.text[:200])
            else:
                logger.info("Webhook sent to %s (%s)", wh_type or "custom", url[:50])
    except Exception as exc:
        logger.warning("Webhook failed: %s", exc)
