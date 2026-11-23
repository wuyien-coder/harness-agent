"""NativeKnowledgeBase：直接调用副本内原项目能力的知识库端口实现。

与旧版独立 harness 的 HTTP / 合成数据适配器不同，这里**直接持有 mathkb 的
AppContext / ChatService / GraphStore**，把原项目已有的真实能力投影到 harness 的
KnowledgeBasePort 契约上：

- search        → ctx.retriever.retrieve（混合检索：向量 + BM25 + 图谱增强）
- answer        → ChatService.ask（原项目完整问答链路，含 ReAct / ReRank / 引用）
- profile       → GraphStore.profile_text
- neighbors     → GraphStore.graph 的有向边（与原项目 ReAct 工具语义一致）
- path          → GraphStore.path（无向最短路径）
- find_people   → GraphStore.by_field / by_hometown

只读保证：本模块只调用原项目的查询方法，不调用任何 ingest / seed / delete 接口；
ChatService.ask 的会话落库发生在 harness 专用库（bootstrap 的 database_url）中，
不写入原项目自带的 `.index/mathkb.db`。
"""
from __future__ import annotations

from typing import Any

from .bootstrap import NativeRuntime
from .contracts import AnswerOutcome, Evidence, SearchOutcome
from .ports import KnowledgeBasePort, UnsupportedCapabilityError
from .stream_events import emit, has_sink

CAPABILITIES = frozenset({"search", "answer", "profile", "neighbors", "path", "find_people"})


class NativeKnowledgeBase(KnowledgeBasePort):
    capabilities = CAPABILITIES

    def __init__(self, runtime: NativeRuntime) -> None:
        self.runtime = runtime
        self.ctx = runtime.ctx
        self.chat = runtime.chat

    # ---- 基础检索 ----
    def search(self, query: str, top_k: int = 5, person: str | None = None) -> SearchOutcome:
        retriever = self.ctx.retriever
        if retriever is None:
            raise UnsupportedCapabilityError("原项目检索器未就绪，请先 build_index()")
        result = retriever.retrieve(query, top_k=top_k, person=person)
        hits: list[Evidence] = []
        for hit in result.hits:
            metadata = dict(getattr(hit, "metadata", {}) or {})
            metadata.update({
                "source": getattr(hit, "source", ""),
                "sources": list(getattr(hit, "sources", []) or []),
                "fused_score": getattr(hit, "fused_score", 0.0),
            })
            hits.append(Evidence(
                doc_id=str(getattr(hit, "doc_id", "")) or self._digest(hit.text),
                text=str(getattr(hit, "text", "")),
                score=float(getattr(hit, "score", 0.0) or 0.0),
                source=str(getattr(hit, "source", "kb_search") or "kb_search"),
                metadata=metadata,
            ))
        return SearchOutcome(
            hits=hits,
            confidence=getattr(result, "confidence", "low") or "low",
            entities=list(getattr(result, "entities", []) or []),
            pool_size=int(getattr(result, "pool_size", len(hits)) or len(hits)),
            timings_ms=dict(getattr(result, "timings_ms", {}) or {}),
        )

    def answer(self, question: str, top_k: int | None = None,
               session_id: str | None = None) -> AnswerOutcome:
        # 有流式通道时走原项目 ChatService.stream：上游真实 token 增量实时推出，
        # 最终 done 事件仍是同一份 ChatAnswer.to_dict()，契约与 ask() 完全一致。
        if has_sink():
            emit("stage", label="正在检索并生成回答", tool="kb_answer")
            overrides = {"top_k": top_k} if top_k else None
            done: dict = {}
            for event in self.chat.stream(
                question, session_id=session_id, mode="rag", engine="auto",
                overrides=overrides,
            ):
                kind = event.get("event")
                if kind == "token":
                    emit("token", text=str(event.get("data", {}).get("text", "")))
                elif kind == "done":
                    done = event.get("data") or {}
            return AnswerOutcome(
                answer=str(done.get("answer") or ""),
                citations=list(done.get("citations") or []),
                confidence=str(done.get("confidence") or "low"),
                entities=list(done.get("entities") or []),
                engine=str(done.get("engine") or ""),
                agent_trace=list(done.get("agent_trace") or []),
                agent_stop=str(done.get("agent_stop") or ""),
                usage=dict(done.get("usage") or {}),
                cost_yuan=float(done.get("cost_yuan") or 0.0),
            )

        chat_answer = self.chat.ask(
            question,
            session_id=session_id,
            mode="rag",
            engine="auto",
            top_k=top_k,
        )
        return AnswerOutcome(
            answer=chat_answer.answer or "",
            citations=list(chat_answer.citations or []),
            confidence=chat_answer.confidence or "low",
            entities=list(chat_answer.entities or []),
            engine=chat_answer.engine or "",
            agent_trace=list(chat_answer.agent_trace or []),
            agent_stop=chat_answer.agent_stop or "",
            usage=dict(chat_answer.usage or {}),
            cost_yuan=float(chat_answer.cost_yuan or 0.0),
        )

    # ---- 图谱结构化能力 ----
    def person_names(self) -> list[str]:
        return self.runtime.person_names

    def _graph(self):
        graph = self.ctx.graph
        if graph is None:
            raise UnsupportedCapabilityError("原项目图谱未加载")
        return graph

    def profile(self, name: str) -> dict:
        graph = self._graph()
        found = name in graph.persons
        return {"name": name, "found": found, "profile": graph.profile_text(name) if found else ""}

    def neighbors(self, name: str, relation: str | None = None,
                  direction: str = "both") -> dict:
        graph = self._graph()
        if name not in graph.graph:
            return {"found": False, "count": 0, "relations": [], "query": {
                "name": name, "relation": relation, "direction": direction},
                "message": "未找到该人物节点"}
        rows: list[dict[str, Any]] = []
        if direction in ("out", "both"):
            for src, dst, data in graph.graph.out_edges(name, data=True):
                rel = data.get("rel", "")
                if relation and rel != relation:
                    continue
                rows.append({"source": src, "target": dst, "relation": rel,
                             "statement": self._statement(src, dst, rel)})
        if direction in ("in", "both"):
            for src, dst, data in graph.graph.in_edges(name, data=True):
                rel = data.get("rel", "")
                if relation and rel != relation:
                    continue
                rows.append({"source": src, "target": dst, "relation": rel,
                             "statement": self._statement(src, dst, rel)})
        return {
            "found": True,
            "count": len(rows),
            "relations": rows[:40],
            "truncated": len(rows) > 40,
            "query": {"name": name, "relation": relation, "direction": direction},
            "message": f"命中 {len(rows)} 条关系" if rows else
                       "人物存在，但当前关系类型/方向下命中 0 条；这不表示此人没有老师，请补查原文",
        }

    @staticmethod
    def _statement(src: str, dst: str, rel: str) -> str:
        if rel == "teacher":
            return f"{src}师从{dst}"
        if rel == "student":
            return f"{src}培养学生{dst}"
        if rel == "advisor":
            return f"{src}的导师是{dst}"
        return f"{src} --{rel}→ {dst}"

    def path(self, source: str, target: str, max_hops: int = 4) -> dict:
        graph = self._graph()
        path = graph.path(source, target, max_hops)
        within = bool(path) and len(path) - 1 <= max_hops
        return {
            "path": path if within else [],
            "within_hops": within,
            "max_hops": max_hops,
            "semantics": "无向连接路径，不代表有向师承",
        }

    def find_people(self, keyword: str, attribute: str = "field",
                    offset: int = 0, limit: int = 20) -> dict:
        graph = self._graph()
        lookup = graph.by_hometown if attribute == "hometown" else graph.by_field
        matches = lookup(keyword, limit=len(graph.persons))
        page = matches[offset:offset + limit]
        return {
            "total_matches": len(matches),
            "names": page,
            "offset": offset,
            "has_more": offset + len(page) < len(matches),
            "matching": "字符串匹配，不是语义分类",
        }

    @staticmethod
    def _digest(text: str) -> str:
        import hashlib
        return "hit:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


__all__ = ["NativeKnowledgeBase", "CAPABILITIES"]
