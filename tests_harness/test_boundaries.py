"""验证新增 harness 与照抄原项目之间的隔离、只读边界与密钥不落盘。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from harness.bootstrap import (
    HARNESS_ROOT,
    INDEX_DB,
    ORIGINAL_INDEX_DB,
    PROJECT_ROOT,
    apply_offline_env,
)
from harness.contracts import AgentRequest, AgentResponse, RunStatus
from harness.runner import Harness
from harness.storage import FileRunStore, InMemoryRunStore

ORIGINAL_PROJECT = Path("D:/找工作文件夹/简历项目_学位论文知识问答_主流架构版")


class BoundaryTests(unittest.TestCase):
    def test_harness_lives_only_in_new_directories(self):
        # 所有 harness 新增源码都在副本的 harness/ 下，不在 src/mathkb 里
        harness_dir = PROJECT_ROOT / "harness"
        self.assertTrue(harness_dir.is_dir())
        src_mathkb = PROJECT_ROOT / "src" / "mathkb"
        intruders = list(src_mathkb.rglob("harness*"))
        self.assertEqual(intruders, [], "不应在 src/mathkb 里新增 harness 文件")
        self.assertEqual(HARNESS_ROOT, PROJECT_ROOT)

    def test_offline_env_is_process_only_not_written_to_env_file(self):
        env_path = PROJECT_ROOT / ".env"
        before = env_path.read_text(encoding="utf-8")
        apply_offline_env()
        self.assertEqual(env_path.read_text(encoding="utf-8"), before,
                         "apply_offline_env 不得修改 .env 文件")
        self.assertEqual(os.environ["MATHKB_VECTOR__PROVIDER"], "memory")
        self.assertEqual(os.environ["MATHKB_CACHE__ENABLED"], "false")
        self.assertIn("harness.db", os.environ["MATHKB_DATABASE_URL"])

    def test_harness_db_is_separate_from_original_index(self):
        self.assertEqual(INDEX_DB.name, "harness.db")
        self.assertEqual(ORIGINAL_INDEX_DB.name, "mathkb.db")
        self.assertNotEqual(INDEX_DB, ORIGINAL_INDEX_DB)

    def test_all_write_paths_are_inside_copy_not_original_project(self):
        """所有 .env 中写死的原项目绝对路径都必须被环境变量覆盖为副本内路径。"""
        env = apply_offline_env()
        write_keys = [k for k in env if any(
            token in k for token in ("DIR", "PERSIST_DIR", "CACHE_DIR", "DATABASE_URL")
        )]
        self.assertTrue(write_keys)
        original = str(ORIGINAL_PROJECT).replace("\\", "/").lower()
        for key in write_keys:
            value = env[key].replace("\\", "/").lower()
            self.assertNotIn(original, value,
                             f"{key} 仍指向原项目目录，会污染原项目: {env[key]}")

    def test_importing_harness_does_not_break_module_isolation(self):
        import harness  # noqa: F401
        # harness 包本身不 import  celery（原项目可选队列），避免隐式外部依赖
        self.assertNotIn("celery", sys.modules)

    def test_offline_end_to_end_with_synthetic_data(self):
        from harness.adapters import InMemoryKnowledgeBase
        harness = Harness(InMemoryKnowledgeBase(), store=InMemoryRunStore())
        response = harness.run("陈景润的老师是谁？")
        self.assertEqual(response.status, RunStatus.COMPLETED)
        self.assertIn("华罗庚", response.answer)
        self.assertGreaterEqual(len(response.evidence), 1)
        self.assertEqual(len(harness.tools), 6)
        self.assertEqual(len(harness.store.responses), 1)

    def test_capability_gating_of_http_adapter(self):
        from harness.adapters import HttpKnowledgeBase
        harness = Harness(HttpKnowledgeBase("http://127.0.0.1:9"))
        self.assertEqual(harness.tools, ["kb_answer", "kb_search"])

    def test_graph_tools_can_be_disabled(self):
        from harness.adapters import InMemoryKnowledgeBase
        harness = Harness(InMemoryKnowledgeBase(), include_graph=False)
        self.assertEqual(harness.tools, ["kb_answer", "kb_search"])

    def test_unknown_tool_in_request_is_rejected(self):
        from harness.adapters import InMemoryKnowledgeBase
        harness = Harness(InMemoryKnowledgeBase())
        with self.assertRaises(ValueError):
            harness.run(AgentRequest(goal="g", tool_allowlist=["not_registered"]))

    def test_file_store_is_confined_to_project_directory(self):
        response = AgentResponse(request_id="unit-test-store", goal="g",
                                 status=RunStatus.COMPLETED, answer="a")
        store = FileRunStore("runs/tests")
        path = store.save(response)
        try:
            self.assertEqual(path.parent, HARNESS_ROOT / "runs" / "tests")
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(encoding="utf-8"), response.to_json())
        finally:
            path.unlink(missing_ok=True)
        outside = str(Path(HARNESS_ROOT).parent / "outside-runs")
        with self.assertRaises(ValueError):
            FileRunStore(outside)

    def test_key_never_persisted_in_harness_files(self):
        """harness 源码与文档中不得出现真实密钥（sk- 前缀）。"""
        harness_dir = PROJECT_ROOT / "harness"
        for path in harness_dir.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".html", ".md"}:
                text = path.read_text(encoding="utf-8")
                # 允许占位符 sk-xxx，但不允许长串真实 key
                import re
                self.assertIsNone(re.search(r"sk-[A-Za-z0-9]{20,}", text),
                                  f"{path} 疑似包含真实密钥")


if __name__ == "__main__":
    unittest.main()
