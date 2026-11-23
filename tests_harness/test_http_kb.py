"""HTTP 边界测试：用注入的 opener 拦截请求，无真实网络。"""
from __future__ import annotations

import json
import unittest

from harness.adapters import HttpKnowledgeBase
from harness.contracts import RunStatus
from harness.runner import Harness

_SEARCH_PAYLOAD = {
    "query": "陈景润",
    "confidence": "high",
    "entities": ["陈景润"],
    "person_filter": None,
    "pool_size": 12,
    "timings_ms": {"total": 3.2},
    "hits": [
        {
            "doc_id": "chunk:abc", "person": "陈景润", "section": "生平", "page": "1",
            "source": "biography", "score": 0.77, "rerank_score": 0.8,
            "sources": ["vector", "keyword"], "text": "陈景润师从华罗庚。",
        }
    ],
}

_CHAT_PAYLOAD = {
    "session_id": "s1",
    "question": "陈景润的老师是谁",
    "answer": "陈景润师从华罗庚。",
    "confidence": "high",
    "entities": ["陈景润", "华罗庚"],
    "mode": "agent",
    "engine": "react",
    "citations": [{"index": 1, "chunk_id": "chunk:abc", "person": "陈景润"}],
    "retrieval": {},
    "timings_ms": {"react": 2.1},
    "usage": {"calls": 3},
    "cost_yuan": 0.05,
    "notes": [],
    "prompt_version": "v2.3",
    "agent_trace": [{"step": 1, "tool": "text_search"}],
    "agent_stop": "finished",
}


class _FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeOpener:
    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(
            (request.full_url, json.loads(request.data.decode("utf-8")), timeout)
        )
        if request.full_url.split("?")[0].endswith("/api/v1/search"):
            return _FakeResponse(_SEARCH_PAYLOAD)
        if request.full_url.split("?")[0].endswith("/api/v1/chat"):
            return _FakeResponse(_CHAT_PAYLOAD)
        raise AssertionError(f"未预期的请求路径: {request.full_url}")


class HttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.opener = _FakeOpener()
        self.kb = HttpKnowledgeBase(
            "https://qa.example.test/?sharecode=ABC123", opener=self.opener
        )

    def test_search_request_and_response_mapping(self):
        outcome = self.kb.search("陈景润", top_k=3, person="陈景润")
        url, payload, timeout = self.opener.requests[0]
        self.assertEqual(
            url, "https://qa.example.test/api/v1/search?sharecode=ABC123"
        )
        self.assertEqual(
            payload, {"query": "陈景润", "top_k": 3, "person": "陈景润"}
        )
        self.assertEqual(timeout, 6.0)
        self.assertEqual(outcome.confidence, "high")
        self.assertEqual(outcome.pool_size, 12)
        self.assertEqual(outcome.hits[0].doc_id, "chunk:abc")
        self.assertEqual(outcome.hits[0].metadata["person"], "陈景润")

    def test_answer_request_and_response_mapping(self):
        outcome = self.kb.answer("陈景润的老师是谁", top_k=4, session_id="s1")
        url, payload, _ = self.opener.requests[0]
        self.assertEqual(url, "https://qa.example.test/api/v1/chat?sharecode=ABC123")
        self.assertEqual(payload["mode"], "agent")
        self.assertEqual(payload["engine"], "auto")
        self.assertEqual(payload["overrides"], {"top_k": 4})
        self.assertEqual(outcome.engine, "react")
        self.assertEqual(outcome.agent_stop, "finished")
        self.assertEqual(outcome.cost_yuan, 0.05)
        self.assertEqual(outcome.citations[0]["index"], 1)

    def test_harness_uses_http_boundary_end_to_end(self):
        harness = Harness(self.kb)
        response = harness.run("陈景润的老师是谁？")
        self.assertEqual(response.status, RunStatus.COMPLETED)
        self.assertEqual(response.answer, "陈景润师从华罗庚。\n\n引用：[1] 陈景润")
        paths = [url.split("?")[0] for url, _, _ in self.opener.requests]
        self.assertIn("https://qa.example.test/api/v1/chat", paths)
        self.assertIn("https://qa.example.test/api/v1/search", paths)
        self.assertEqual(len(harness.tools), 2)


if __name__ == "__main__":
    unittest.main()
