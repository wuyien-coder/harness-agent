"""大模型通道测试：全部用注入的假客户端/ opener，无真实网络与费用。"""
from __future__ import annotations

import io
import json
import unittest

from harness.adapters import (
    InMemoryKnowledgeBase,
    LLMConfig,
    LLMPlanner,
    LLMResponder,
    OpenAICompatClient,
    parse_json_object,
)
from harness.contracts import AgentRequest, Evidence, RunStatus
from harness.registry import ToolRegistry
from harness.runner import Harness

from ._helpers import BoomTool, EchoTool


class FakeLLMClient:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, *, temperature=0.0, max_tokens=None):
        self.calls.append({
            "messages": messages, "temperature": temperature, "max_tokens": max_tokens
        })
        return self.responses.pop(0) if self.responses else ""


class _FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Opener:
    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        return _FakeResponse(
            {"choices": [{"message": {"content": "模型回答"}}]}
        )


class LLMTests(unittest.TestCase):
    def test_openai_client_request_and_response(self):
        opener = _Opener()
        client = OpenAICompatClient(
            LLMConfig(base_url="https://api.deepseek.com/v1", api_key="sk-test",
                      model="deepseek-v4-pro"),
            opener=opener,
        )
        result = client.chat(
            [{"role": "user", "content": "你好"}], temperature=0.3, max_tokens=42
        )
        self.assertEqual(result, "模型回答")
        request, timeout = opener.requests[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer sk-test")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertEqual(payload["max_tokens"], 42)
        self.assertEqual(payload["temperature"], 0.3)
        self.assertEqual(timeout, 60.0)
        with self.assertRaises(RuntimeError):
            OpenAICompatClient(LLMConfig(api_key="")).chat([])

    def test_openai_client_stream_chat_yields_sse_deltas(self):
        sse = (
            b'data: {"choices":[{"delta":{"content":"\xe9\x99\x88\xe6\x99\xaf"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"\xe6\xb6\xa6"}}]}\n\n'
            b'data: [DONE]\n\n'
        )

        class StreamOpener:
            def __init__(self):
                self.payloads = []

            def __call__(self, request, timeout=None):
                self.payloads.append(json.loads(request.data.decode("utf-8")))
                return io.BytesIO(sse)

        opener = StreamOpener()
        client = OpenAICompatClient(LLMConfig(api_key="sk-test"), opener=opener)
        deltas = list(client.stream_chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(deltas, ["陈景", "润"])
        self.assertTrue(opener.payloads[0]["stream"])
        self.assertEqual(opener.payloads[0]["stream_options"], {"include_usage": True})

    def test_api_key_is_redacted_from_errors(self):
        class BadOpener:
            def __call__(self, request, timeout=None):
                raise RuntimeError("invalid header Bearer sk-test")

        client = OpenAICompatClient(
            LLMConfig(api_key="sk-test"), opener=BadOpener()
        )
        with self.assertRaises(RuntimeError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertNotIn("sk-test", str(ctx.exception))
        self.assertIn("***", str(ctx.exception))
        with self.assertRaises(RuntimeError) as ctx2:
            list(client.stream_chat([{"role": "user", "content": "hi"}]))
        self.assertNotIn("sk-test", str(ctx2.exception))

    def test_parse_json_object_tolerates_fence_and_noise(self):
        raw = '说明：\n```json\n{"action": "finish", "answer": "答案"}\n```\n结束'
        self.assertEqual(parse_json_object(raw)["answer"], "答案")
        with self.assertRaises(ValueError):
            parse_json_object("不是 JSON")

    def test_planner_filters_unknown_tools_and_caps(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(BoomTool())
        llm = FakeLLMClient(json.dumps({
            "action": "dispatch",
            "tasks": [
                {"id": "a", "tool": "echo", "arguments": {"text": "1"}},
                {"id": "b", "tool": "ghost", "arguments": {}},
                {"id": "c", "tool": "boom", "arguments": {}},
                {"id": "d", "tool": "echo", "arguments": {"text": "2"}},
            ],
        }))
        planner = LLMPlanner(llm, registry.describe(), max_tasks=2)
        decision = planner.plan(AgentRequest(goal="g"), [], 1)
        self.assertEqual(decision.action, "dispatch")
        self.assertEqual([task.tool for task in decision.tasks], ["echo", "boom"])
        self.assertIn("echo", llm.calls[0]["messages"][0]["content"])

    def test_planner_finish_and_fallbacks(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        planner = LLMPlanner(FakeLLMClient('{"action":"finish","answer":"直接答"}'),
                             registry.describe())
        self.assertEqual(planner.plan(AgentRequest(goal="g"), [], 1).answer, "直接答")

        planner = LLMPlanner(FakeLLMClient("bad json"), registry.describe())
        self.assertEqual(
            planner.plan(AgentRequest(goal="g"), [{"task_id": "t1"}], 2).action, "finish"
        )
        planner = LLMPlanner(FakeLLMClient("bad json"), registry.describe())
        self.assertEqual(planner.plan(AgentRequest(goal="g"), [], 1).action, "abort")

    def test_responder_uses_evidence(self):
        llm = FakeLLMClient("基于证据的答案 [1]")
        responder = LLMResponder(llm)
        answer = responder.respond(
            AgentRequest(goal="g"), [Evidence("d1", "证据原文", source="kb_search")], []
        )
        self.assertEqual(answer, "基于证据的答案 [1]")
        self.assertIn("[1]", llm.calls[0]["messages"][1]["content"])
        llm2 = FakeLLMClient("不该被调用")
        self.assertEqual(
            LLMResponder(llm2).respond(AgentRequest(goal="g"), [], []),
            "证据不足，无法回答该问题。",
        )
        self.assertEqual(len(llm2.calls), 0)

    def test_harness_uses_llm_for_planning_and_answer(self):
        llm = FakeLLMClient(
            json.dumps({
                "action": "dispatch",
                "tasks": [
                    {"id": "t1", "tool": "kb_answer",
                     "arguments": {"question": "陈景润的老师是谁"}},
                    {"id": "t2", "tool": "kb_search", "arguments": {"query": "陈景润"}},
                ],
            }),
            '{"action":"finish","answer":""}',
            "大模型给出的最终答案",
        )
        harness = Harness(InMemoryKnowledgeBase(), llm=llm)
        response = harness.run(AgentRequest(goal="陈景润的老师是谁？"))
        self.assertEqual(response.status, RunStatus.COMPLETED)
        self.assertEqual(response.answer, "大模型给出的最终答案")
        self.assertEqual(len(llm.calls), 3)
        self.assertEqual(response.tool_calls_count, 2)
        self.assertEqual(harness.planner.name, "llm")
        self.assertEqual(harness.responder.name, "llm")


if __name__ == "__main__":
    unittest.main()
