"""Knowledge-search CLI validation and project Markdown fallback tests."""
from __future__ import annotations

import subprocess
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from agent import test_kb
from core import kb_search
from core.test_agent import TestCaseAgent
from core.kb_platform import allows_document, filter_cli_knowledge


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class KnowledgeSearchCliTests(unittest.TestCase):
    def test_macos_uses_system_ca_without_overriding_explicit_ca(self):
        import os
        with (
            patch.object(kb_search.sys, "platform", "darwin"),
            patch.object(kb_search.os.path, "isfile", return_value=True),
            patch.object(kb_search.shutil, "which", return_value="/usr/local/bin/kb-search"),
            patch.object(kb_search.subprocess, "run", return_value=_completed("result")) as run,
            patch.dict(os.environ, {}, clear=True),
        ):
            kb_search.run_kb_search_result("kb-search", "入口")
            self.assertEqual(run.call_args.kwargs["env"]["SSL_CERT_FILE"], "/etc/ssl/cert.pem")
            os.environ["SSL_CERT_FILE"] = "/custom/ca.pem"
            kb_search.run_kb_search_result("kb-search", "入口")
            self.assertEqual(run.call_args.kwargs["env"]["SSL_CERT_FILE"], "/custom/ca.pem")

    def test_filters_before_budget_so_android_hit_survives_long_harmony_hit(self):
        output = ("Search: 入口\n### /knowledge/platforms/harmony/nav.md\n# 鸿蒙导航\n"
                  + "x" * 5000 + "\n### /knowledge/platforms/android/nav.md\n# Android 导航\n正确入口")
        with (
            patch.object(kb_search.shutil, "which", return_value="/usr/local/bin/kb-search"),
            patch.object(kb_search.subprocess, "run", return_value=_completed(output)),
        ):
            result = kb_search.run_kb_search_result("kb-search", "入口", platform="android")
        self.assertIn("正确入口", result.text)
        self.assertNotIn("鸿蒙导航", result.text)
        self.assertEqual(result.excluded, 1)
        self.assertEqual(result.sources, ("/knowledge/platforms/android/nav.md",))

    def test_accepts_successful_output(self):
        with (
            patch.object(kb_search.shutil, "which", return_value="/usr/local/bin/kb-search"),
            patch.object(kb_search.subprocess, "run", return_value=_completed("knowledge result")),
        ):
            result = kb_search.run_kb_search_result("kb-search", "修仙")

        self.assertTrue(result.succeeded)
        self.assertEqual(result.text, "knowledge result")
        self.assertEqual(result.error, "")

    def test_rejects_zero_exit_validation_error(self):
        output = (
            "Search: 修仙\nError executing tool search_lore: "
            "1 validation error for TextNode\nid_ input_value=nan"
        )
        with (
            patch.object(kb_search.shutil, "which", return_value="/usr/local/bin/kb-search"),
            patch.object(kb_search.subprocess, "run", return_value=_completed(output)),
        ):
            result = kb_search.run_kb_search_result("kb-search", "修仙")
            compatible_text = kb_search.run_kb_search("kb-search", "修仙")

        self.assertFalse(result.succeeded)
        self.assertEqual(result.text, "")
        self.assertIn("validation error", result.error)
        self.assertEqual(compatible_text, "")

    def test_rejects_nonzero_exit(self):
        with (
            patch.object(kb_search.shutil, "which", return_value="/usr/local/bin/kb-search"),
            patch.object(
                kb_search.subprocess,
                "run",
                return_value=_completed(stderr="service unavailable", returncode=2),
            ),
        ):
            result = kb_search.run_kb_search_result("kb-search", "修仙")

        self.assertFalse(result.succeeded)
        self.assertIn("exited with 2", result.error)


class AgentKnowledgeFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_audit_is_saved_alongside_live_logs(self):
        agent = object.__new__(TestCaseAgent)
        agent.login_context = None
        agent.log_callback = None
        agent._kb_audit_logs = []
        await agent._log("  📚 KB search: platform=android, query=超级用户")
        await agent._log("unrelated progress")
        self.assertEqual(agent._kb_audit_logs, ["  📚 KB search: platform=android, query=超级用户"])

    async def test_falls_back_when_all_cli_hits_are_other_platforms(self):
        agent = object.__new__(TestCaseAgent)
        agent.platform = "android"
        agent.kb_search_cmd = "kb-search"
        with tempfile.TemporaryDirectory() as root:
            for platform, title in [("android", "Android 超级用户"), ("harmony", "鸿蒙 超级用户")]:
                folder = Path(root) / "platforms" / platform
                folder.mkdir(parents=True)
                (folder / "entry.md").write_text(f"# {title}\n\n## 入口路径\n{platform}入口", encoding="utf-8")
            agent.project_kb_roots = [root]
            with (
                patch.object(kb_search, "run_kb_search_result", return_value=kb_search.KBSearchResult(excluded=2)),
                patch.object(test_kb, "_load_index", return_value=[]),
                patch.object(test_kb, "_index_notes", return_value=[]),
            ):
                text, log = await agent._retrieve_kb("超级用户")
            self.assertIn("android入口", text)
            self.assertNotIn("harmony入口", text)
            self.assertIn("platform=android", log)
            self.assertIn("排除其他平台 2 条", log)

    async def test_skips_project_markdown_after_cli_success(self):
        agent = object.__new__(TestCaseAgent)
        agent.kb_search_cmd = "kb-search"
        agent.project_kb_roots = ["/project/kb"]
        seen_roots = []

        def fake_search(query, extra_roots=None, matches_out=None, **kwargs):
            seen_roots.append(extra_roots)
            return ""

        with (
            patch.object(
                kb_search,
                "run_kb_search_result",
                return_value=kb_search.KBSearchResult(text="CLI result"),
            ),
            patch.object(test_kb, "search_feature", side_effect=fake_search),
        ):
            text, source = await agent._retrieve_kb("修仙")

        self.assertEqual(text, "CLI result")
        self.assertEqual(seen_roots, [[]])
        self.assertIn("项目检索", source)

    async def test_falls_back_without_injecting_cli_error(self):
        agent = object.__new__(TestCaseAgent)
        agent.kb_search_cmd = "kb-search"
        agent.project_kb_roots = ["/project/kb"]
        seen_roots = []

        def fake_search(query, extra_roots=None, matches_out=None, **kwargs):
            seen_roots.append(extra_roots)
            matches_out.append({
                "title": "修仙宗门玩法",
                "slug": "xiuxian",
                "source": "project",
                "score": 20,
            })
            return "Markdown fallback"

        with (
            patch.object(
                kb_search,
                "run_kb_search_result",
                return_value=kb_search.KBSearchResult(error="TextNode id is NaN"),
            ),
            patch.object(test_kb, "search_feature", side_effect=fake_search),
        ):
            text, source = await agent._retrieve_kb("修仙")

        self.assertEqual(text, "Markdown fallback")
        self.assertNotIn("TextNode", text)
        self.assertEqual(seen_roots, [["/project/kb"]])
        self.assertIn("失败，已回退", source)


class PlatformKnowledgeTests(unittest.TestCase):
    def test_shared_business_body_can_mention_other_platforms(self):
        self.assertTrue(allows_document("android", "/knowledge/business/login.md", "登录业务", "iOS 和鸿蒙也支持登录"))
        self.assertFalse(allows_document("android", "/knowledge/platforms/ios/login.md", "登录业务"))
        self.assertFalse(allows_document("android", title="鸿蒙模块通信与页面导航"))
        self.assertTrue(allows_document("harmony", title="鸿蒙模块通信与页面导航"))

    def test_metadata_supports_shared_and_multiple_platforms(self):
        self.assertTrue(allows_document("android", content="---\nplatform: [android, ios]\n---\n# 登录"))
        self.assertFalse(allows_document("harmony", content="---\nplatform: [android, ios]\n---\n# 登录"))
        self.assertTrue(allows_document("android", content="platform: shared\n# 文档"))

    def test_full_read_cannot_bypass_platform_filter(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "platforms" / "harmony"
            folder.mkdir(parents=True)
            (folder / "nav.md").write_text("# 页面导航\n鸿蒙专用路径", encoding="utf-8")
            text = kb_search.read_kb_doc([root], "nav.md", "android")
            self.assertIn("KB doc excluded", text)
            self.assertNotIn("鸿蒙专用路径", text)
            self.assertIn("鸿蒙专用路径", kb_search.read_kb_doc([root], "nav.md", "harmony"))
