"""把 KnowledgeBasePort 的能力投影成一组标准工具。

工具全部只读；参数 schema 与原问答项目的 ReAct 工具保持语义一致，
但这里是独立实现，不 import 原项目代码。
"""
from __future__ import annotations

import hashlib
import json

from .contracts import Evidence, ToolResult
from .ports import KnowledgeBasePort, RunContext, ToolPort

SEARCH_TOOL = "kb_search"
ANSWER_TOOL = "kb_answer"
GRAPH_TOOLS = ("kb_profile", "kb_neighbors", "kb_path", "kb_find_people")


class _KbTool(ToolPort):
    def __init__(self, name: str, description: str, parameters: dict, handler) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self._handler = handler

    def invoke(self, arguments: dict, context: RunContext) -> ToolResult:
        return self._handler(arguments, context)


def _digest(*parts) -> str:
    return hashlib.sha256(
        json.dumps(list(parts), ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def build_kb_tools(kb: KnowledgeBasePort, *, include_graph: bool = True) -> list[ToolPort]:
    capabilities = set(kb.capabilities)

    def search_tool(args: dict, _ctx: RunContext) -> ToolResult:
        outcome = kb.search(
            query=args["query"], top_k=args.get("top_k", 5), person=args.get("person")
        )
        hits = [
            Evidence(
                doc_id=hit.doc_id or "hit:" + _digest(hit.text),
                text=hit.text,
                score=hit.score,
                source=hit.source or "kb_search",
                metadata=dict(hit.metadata),
            )
            for hit in outcome.hits
        ]
        return ToolResult(
            ok=True,
            observation={
                "count": len(hits),
                "confidence": outcome.confidence,
                "entities": outcome.entities,
                "hits": [
                    {"id": h.doc_id, "text": h.text[:900], "score": h.score, "source": h.source}
                    for h in hits
                ],
            },
            evidence=hits,
        )

    def answer_tool(args: dict, _ctx: RunContext) -> ToolResult:
        outcome = kb.answer(
            question=args["question"],
            top_k=args.get("top_k"),
            session_id=args.get("session_id"),
        )
        evidence = Evidence(
            doc_id="kb-answer:" + _digest(outcome.answer, args["question"]),
            text=outcome.answer,
            score=1.0,
            source="kb_answer/" + (outcome.engine or "unknown"),
            metadata={
                "citations": outcome.citations,
                "confidence": outcome.confidence,
                "entities": outcome.entities,
                "agent_trace": outcome.agent_trace,
                "agent_stop": outcome.agent_stop,
                "usage": outcome.usage,
                "cost_yuan": outcome.cost_yuan,
            },
        )
        return ToolResult(
            ok=True,
            observation={
                "answer": outcome.answer,
                "confidence": outcome.confidence,
                "citations": outcome.citations,
                "engine": outcome.engine,
            },
            evidence=[evidence] if outcome.answer else [],
        )

    tools: list[ToolPort] = []
    if "search" in capabilities:
        tools.append(
            _KbTool(
                SEARCH_TOOL,
                "按关键词/语义检索知识库原文，可按人物限定。",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 300},
                        "person": {"type": "string", "maxLength": 100},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                search_tool,
            )
        )
    if "answer" in capabilities:
        tools.append(
            _KbTool(
                ANSWER_TOOL,
                "向知识问答能力提出完整问题，返回带引用的回答。",
                {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "minLength": 1, "maxLength": 500},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                        "session_id": {"type": "string", "maxLength": 100},
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
                answer_tool,
            )
        )

    if include_graph:
        if "profile" in capabilities:
            tools.append(
                _KbTool(
                    "kb_profile", "查询某人物的结构化画像。",
                    {"type": "object", "properties": {
                        "name": {"type": "string", "minLength": 1, "maxLength": 100}},
                     "required": ["name"], "additionalProperties": False},
                    lambda args, _ctx: ToolResult(
                        ok=True, observation=kb.profile(args["name"])
                    ),
                )
            )
        if "neighbors" in capabilities:
            tools.append(
                _KbTool(
                    "kb_neighbors", "查询人物的有向关系边。",
                    {"type": "object", "properties": {
                        "name": {"type": "string", "minLength": 1, "maxLength": 100},
                        "relation": {"type": "string", "enum": ["teacher", "student", "advisor"]},
                        "direction": {"type": "string", "enum": ["in", "out", "both"]}},
                     "required": ["name"], "additionalProperties": False},
                    lambda args, _ctx: ToolResult(
                        ok=True,
                        observation=kb.neighbors(
                            args["name"], args.get("relation"), args.get("direction", "both")
                        ),
                    ),
                )
            )
        if "path" in capabilities:
            tools.append(
                _KbTool(
                    "kb_path", "查询两人之间的无向连接路径。",
                    {"type": "object", "properties": {
                        "source": {"type": "string", "minLength": 1, "maxLength": 100},
                        "target": {"type": "string", "minLength": 1, "maxLength": 100},
                        "max_hops": {"type": "integer", "minimum": 1, "maximum": 6}},
                     "required": ["source", "target"], "additionalProperties": False},
                    lambda args, _ctx: ToolResult(
                        ok=True,
                        observation=kb.path(args["source"], args["target"], args.get("max_hops", 4)),
                    ),
                )
            )
        if "find_people" in capabilities:
            tools.append(
                _KbTool(
                    "kb_find_people", "按领域或籍贯做字符串匹配并分页返回人物。",
                    {"type": "object", "properties": {
                        "keyword": {"type": "string", "minLength": 1, "maxLength": 100},
                        "attribute": {"type": "string", "enum": ["field", "hometown"]},
                        "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 30}},
                     "required": ["keyword"], "additionalProperties": False},
                    lambda args, _ctx: ToolResult(
                        ok=True,
                        observation=kb.find_people(
                            args["keyword"], args.get("attribute", "field"),
                            args.get("offset", 0), args.get("limit", 20),
                        ),
                    ),
                )
            )
    return tools


__all__ = ["build_kb_tools", "SEARCH_TOOL", "ANSWER_TOOL", "GRAPH_TOOLS"]
