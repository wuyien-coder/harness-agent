"""NativeKnowledgeBase 直连原项目能力的契约测试（全部 mock，无真实网络/索引）。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from harness.bootstrap import NativeRuntime
from harness.contracts import SearchOutcome
from harness.native_kb import CAPABILITIES, NativeKnowledgeBase
from harness.ports import UnsupportedCapabilityError


class FakeGraph:
    def __init__(self):
        import networkx as nx
        self.persons = {
            "陈景润": SimpleNamespace(name="陈景润", hometown="福建", fields=["解析数论"],
                                   institutions=["中科院"], achievements="陈氏定理", birth_year="1933"),
            "华罗庚": SimpleNamespace(name="华罗庚", hometown="江苏", fields=["数论"],
                                   institutions=["中科院"], achievements="华氏定理", birth_year="1910"),
        }
        self.graph = nx.DiGraph()
        self.graph.add_node("陈景润")
        self.graph.add_node("华罗庚")
        # teacher 边：学生 -> 老师（陈景润 师从 华罗庚）
        self.graph.add_edge("陈景润", "华罗庚", rel="teacher")
        self.graph.add_edge("华罗庚", "陈景润", rel="student")

    @property
    def names(self):
        return list(self.persons)

    def profile_text(self, name):
        p = self.persons.get(name)
        return f"{name}；籍贯 {p.hometown}" if p else ""

    def path(self, a, b, max_hops=4):
        return ["陈景润", "华罗庚"] if {a, b} == {"陈景润", "华罗庚"} else []

    def by_field(self, keyword, limit=20):
        return [n for n, p in self.persons.items() if any(keyword in f for f in p.fields)][:limit]

    def by_hometown(self, keyword, limit=20):
        return [n for n, p in self.persons.items() if keyword in p.hometown][:limit]


class FakeRetriever:
    def retrieve(self, query, top_k=None, person=None):
        hit = SimpleNamespace(
            doc_id="chunk:1", text="陈景润师从华罗庚", score=0.42, source="vector",
            metadata={"person_name": "陈景润"}, sources=["vector", "keyword"], fused_score=0.5,
        )
        return SimpleNamespace(
            hits=[hit], confidence="high", entities=["陈景润"], pool_size=1, timings_ms={"retrieve": 3.2}
        )


class FakeChat:
    def __init__(self):
        self.calls = []

    @staticmethod
    def _answer_dict():
        return {
            "answer": "陈景润师从华罗庚", "citations": [{"index": 1, "chunk_id": "chunk:1"}],
            "confidence": "high", "entities": ["陈景润", "华罗庚"], "engine": "react",
            "agent_trace": [{"step": 1}], "agent_stop": "finished",
            "usage": {"total_tokens": 42}, "cost_yuan": 0.01,
        }

    def ask(self, question, **kwargs):
        self.calls.append((question, kwargs))
        return SimpleNamespace(**self._answer_dict())

    def stream(self, question, **kwargs):
        self.calls.append((question, kwargs))
        yield {"event": "retrieval", "data": {"confidence": "high"}}
        yield {"event": "token", "data": {"text": "陈景润"}}
        yield {"event": "token", "data": {"text": "师从华罗庚"}}
        yield {"event": "done", "data": self._answer_dict()}


def make_kb():
    ctx = SimpleNamespace(retriever=FakeRetriever(), graph=FakeGraph())
    runtime = NativeRuntime(ctx=ctx, chat=FakeChat(), index_stats={"chunks": 2})
    return NativeKnowledgeBase(runtime), runtime


class NativeKbTests(unittest.TestCase):
    def test_capabilities_include_all_six_real_tools(self):
        self.assertEqual(set(CAPABILITIES),
                         {"search", "answer", "profile", "neighbors", "path", "find_people"})
        self.assertEqual(set(NativeKnowledgeBase.capabilities), set(CAPABILITIES))

    def test_search_maps_original_hit_to_evidence(self):
        kb, _ = make_kb()
        outcome = kb.search("陈景润的老师", top_k=3, person="陈景润")
        self.assertIsInstance(outcome, SearchOutcome)
        self.assertEqual(outcome.hits[0].doc_id, "chunk:1")
        self.assertEqual(outcome.hits[0].text, "陈景润师从华罗庚")
        self.assertEqual(outcome.hits[0].metadata["source"], "vector")
        self.assertEqual(outcome.hits[0].metadata["sources"], ["vector", "keyword"])
        self.assertEqual(outcome.confidence, "high")
        self.assertEqual(outcome.entities, ["陈景润"])

    def test_answer_streams_real_upstream_tokens_when_sink_active(self):
        from harness.stream_events import stream_sink

        kb, runtime = make_kb()
        events = []
        with stream_sink(events.append):
            outcome = kb.answer("陈景润的老师是谁？", top_k=5)
        self.assertEqual(outcome.answer, "陈景润师从华罗庚")
        self.assertEqual(outcome.engine, "react")
        self.assertEqual(outcome.citations[0]["chunk_id"], "chunk:1")
        kinds = [event["event"] for event in events]
        self.assertEqual(kinds[0], "stage")
        self.assertEqual(kinds.count("token"), 2)
        joined = "".join(e["text"] for e in events if e["event"] == "token")
        self.assertEqual(joined, outcome.answer)
        self.assertEqual(len(runtime.chat.calls), 1)

    def test_answer_delegates_to_chat_service_with_real_contract(self):
        kb, runtime = make_kb()
        outcome = kb.answer("陈景润的老师是谁？", top_k=5, session_id="sess-1")
        self.assertEqual(outcome.answer, "陈景润师从华罗庚")
        self.assertEqual(outcome.engine, "react")
        self.assertEqual(outcome.citations[0]["chunk_id"], "chunk:1")
        self.assertEqual(outcome.cost_yuan, 0.01)
        question, kwargs = runtime.chat.calls[0]
        self.assertEqual(question, "陈景润的老师是谁？")
        self.assertEqual(kwargs["session_id"], "sess-1")
        self.assertEqual(kwargs["mode"], "rag")
        self.assertEqual(kwargs["top_k"], 5)

    def test_profile_and_find_people(self):
        kb, _ = make_kb()
        self.assertTrue(kb.profile("陈景润")["found"])
        self.assertIn("籍贯 福建", kb.profile("陈景润")["profile"])
        self.assertFalse(kb.profile("不存在")["found"])
        self.assertIn("陈景润", kb.find_people("解析数论", "field")["names"])
        self.assertIn("陈景润", kb.find_people("福建", "hometown")["names"])

    def test_neighbors_respects_relation_and_direction(self):
        kb, _ = make_kb()
        out_teacher = kb.neighbors("陈景润", relation="teacher", direction="out")
        self.assertEqual(out_teacher["count"], 1)
        self.assertEqual(out_teacher["relations"][0]["target"], "华罗庚")
        self.assertIn("师从", out_teacher["relations"][0]["statement"])
        in_student = kb.neighbors("陈景润", relation="student", direction="in")
        self.assertEqual(in_student["relations"][0]["source"], "华罗庚")
        none = kb.neighbors("陈景润", relation="advisor", direction="both")
        self.assertEqual(none["count"], 0)
        self.assertIn("请补查原文", none["message"])
        self.assertFalse(kb.neighbors("不存在")["found"])

    def test_path_semantics_and_hops(self):
        kb, _ = make_kb()
        result = kb.path("陈景润", "华罗庚", max_hops=2)
        self.assertTrue(result["within_hops"])
        self.assertEqual(len(result["path"]), 2)  # 1 跳
        self.assertIn("不代表有向师承", result["semantics"])
        self.assertTrue(kb.path("陈景润", "华罗庚", max_hops=1)["within_hops"])  # 恰好 1 跳
        self.assertEqual(kb.path("陈景润", "不存在")["path"], [])  # 无路径

    def test_person_names_from_graph(self):
        kb, _ = make_kb()
        self.assertEqual(set(kb.person_names()), {"陈景润", "华罗庚"})

    def test_missing_graph_raises_unsupported(self):
        ctx = SimpleNamespace(retriever=FakeRetriever(), graph=None)
        kb = NativeKnowledgeBase(NativeRuntime(ctx=ctx, chat=FakeChat(), index_stats={}))
        with self.assertRaises(UnsupportedCapabilityError):
            kb.profile("陈景润")

    def test_no_ingest_or_delete_methods_exposed(self):
        kb, _ = make_kb()
        for forbidden in ("ingest", "seed", "delete", "upload", "write"):
            self.assertFalse(any(forbidden in name for name in dir(kb)),
                             f"只读适配器不应暴露 {forbidden}")


if __name__ == "__main__":
    unittest.main()
