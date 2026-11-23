"""面板呈现契约测试：extract_presentation 引用归一化 + 前端中文化映射完整性。

借鉴原面板呈现依赖固定的字段契约，这里守护：
- 后端把 kb_answer 证据里的 citations/confidence/agent_trace 提到顶层；
- 前端中文化映射覆盖原项目工具名与 harness kb_* 工具名（静态扫描）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from harness.panel.server import extract_presentation, sse_event

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PANEL_HTML = PROJECT_ROOT / "harness" / "panel" / "index.html"


class ExtractPresentationTests(unittest.TestCase):
    def test_extracts_kb_answer_metadata_to_top_level(self):
        response = {"evidence": [{
            "source": "kb_answer/sequential",
            "metadata": {
                "citations": [{"index": 1, "person": "苏步青", "chunk_id": "graph:0"}],
                "confidence": "high",
                "agent_trace": [{"step": 1, "tool": "text_search"}],
                "agent_stop": "finish",
                "cost_yuan": 0.05,
            },
        }]}
        out = extract_presentation(response)
        self.assertEqual(out["citations"][0]["person"], "苏步青")
        self.assertEqual(out["confidence"], "high")
        self.assertEqual(out["engine"], "sequential")  # source split on '/'
        self.assertEqual(len(out["agent_trace"]), 1)
        self.assertEqual(out["agent_stop"], "finish")
        self.assertAlmostEqual(out["cost_yuan"], 0.05)

    def test_engine_without_slash_uses_source(self):
        response = {"evidence": [{
            "source": "kb_answer",
            "metadata": {"citations": [], "agent_trace": []},
        }]}
        self.assertEqual(extract_presentation(response)["engine"], "kb_answer")

    def test_ignores_non_answer_evidence(self):
        response = {"evidence": [
            {"source": "vector", "metadata": {"person_name": "x"}},
            {"source": "kb_search", "metadata": {"count": 5}},
        ]}
        out = extract_presentation(response)
        self.assertEqual(out["citations"], [])
        self.assertEqual(out["agent_trace"], [])
        self.assertEqual(out["engine"], "")

    def test_empty_response(self):
        out = extract_presentation({})
        self.assertEqual(out, {
            "citations": [], "confidence": "", "engine": "",
            "agent_trace": [], "agent_stop": "", "cost_yuan": 0.0,
        })


class SSEFrameTests(unittest.TestCase):
    def test_sse_event_format(self):
        frame = sse_event({"event": "token", "text": "你好"}).decode("utf-8")
        self.assertTrue(frame.startswith("event: token\ndata: "))
        self.assertTrue(frame.endswith("\n\n"))
        self.assertIn('"text": "你好"', frame)
        self.assertNotIn('"event": "token"', frame)  # event 只在事件名行出现

    def test_done_payload_is_flattened(self):
        frame = sse_event({"event": "done", "data": {"status": "completed", "answer": "答"}})
        self.assertIn('"status": "completed"', frame.decode("utf-8"))
        self.assertNotIn('"data": {', frame.decode("utf-8"))

    def test_sse_event_escapes_unicode_and_quotes(self):
        frame = sse_event({"event": "done", "data": {"answer": '他说"你好"'}}).decode("utf-8")
        self.assertIn('\\"你好\\"', frame)


class PanelPresentationWiringTests(unittest.TestCase):
    """静态守护：前端必须具备借鉴原面板的关键呈现元素。"""

    @classmethod
    def setUpClass(cls):
        cls.html = PANEL_HTML.read_text(encoding="utf-8")

    def test_citation_card_elements_present(self):
        for needle in ("citation-card", "reference-number", "source-kind",
                       "renderMarkdown", "evidence-meta", "agent-trace",
                       "cite-ref", "confidenceLabel", "toolLabels"):
            self.assertIn(needle, self.html, f"缺少呈现元素: {needle}")

    def test_tool_labels_cover_original_and_harness(self):
        # 原项目 ReAct 工具名
        for name in ("text_search", "person_profile", "graph_neighbors", "graph_path", "find_people"):
            self.assertIn(f"{name}:", self.html, f"缺少原工具名映射: {name}")
        # harness kb_* 工具名
        for name in ("kb_search", "kb_answer", "kb_profile", "kb_neighbors", "kb_path", "kb_find_people"):
            self.assertIn(f"{name}:", self.html, f"缺少 harness 工具名映射: {name}")

    def test_graph_citation_detection(self):
        self.assertIn("startsWith('graph:')", self.html)
        self.assertIn("startsWith('react-graph:')", self.html)

    def test_streaming_wiring_present(self):
        for needle in ("/api/stream", "consumeSSE", "addStreamingPlaceholder",
                       "stream-status", "stream-cursor", "name === 'token'"):
            self.assertIn(needle, self.html, f"缺少流式呈现元素: {needle}")

    def test_interaction_wiring_present(self):
        """借鉴原面板的交互：停止生成 / 输入法保护 / 消息切换引用 / 重问 / 通知。"""
        for needle in (
            "AbortController", "controller.signal", "stopBtn", "停止生成",
            "e.isComposing", "e.keyCode !== 229", "syncSendButton",
            "selectMessage", "answerMeta", "dataset.msgId", "reask-btn",
            "已停止生成", "noticeBar", "composer-disclaimer",
        ):
            self.assertIn(needle, self.html, f"缺少交互接线元素: {needle}")

    def test_send_button_disabled_when_empty(self):
        self.assertIn("const empty = !questionBox.value.trim()", self.html)
        self.assertIn("$('sendBtn').disabled = empty || busy", self.html)

    def test_prompt_buttons_use_explicit_question(self):
        self.assertEqual(self.html.count('class="prompt-btn" data-question='), 3)

    def test_no_external_cdn_dependency(self):
        # 面板须纯静态、可离线：不得引用外网脚本/样式
        self.assertIsNone(re.search(r'<script[^>]+src=["\']https?://', self.html))
        self.assertIsNone(re.search(r'<link[^>]+href=["\']https?://', self.html))


if __name__ == "__main__":
    unittest.main()
