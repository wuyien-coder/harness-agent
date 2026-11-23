"""原知识问答项目的 HTTP 适配边界。

本模块不 import 原项目任何代码，只依赖其公开 HTTP API 契约：
  POST /api/v1/search -> SearchResponse
  POST /api/v1/chat   -> ChatResponse
通过 base_url 与 opener 注入，可指向本地服务、线上服务或测试桩。
"""
from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from ..contracts import AnswerOutcome, Evidence, SearchOutcome
from ..ports import KnowledgeBasePort


class HttpKnowledgeBase(KnowledgeBasePort):
    """只声明公开 API 实际支持的能力；结构化图谱工具不在此注册。"""

    capabilities = frozenset({"search", "answer"})

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 6.0,
        opener=None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        # 保留 base_url 上的查询串（例如公开部署的 ?sharecode=xxx），拼到每个 API 路径后
        self.base_url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path.rstrip("/"), "", "")
        )
        self.query = parts.query
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen
        self._headers = {"Content-Type": "application/json", **(extra_headers or {})}

    def _post(self, path: str, payload: dict) -> dict:
        url = self.base_url + path
        if self.query:
            url += "?" + self.query
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=dict(self._headers),
            method="POST",
        )
        with self._opener(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def search(self, query: str, top_k: int = 5, person: str | None = None) -> SearchOutcome:
        payload: dict = {"query": query, "top_k": top_k}
        if person:
            payload["person"] = person
        raw = self._post("/api/v1/search", payload)
        hits = []
        for item in raw.get("hits", []):
            metadata = {
                "person": item.get("person", ""),
                "section": item.get("section", ""),
                "page": item.get("page", ""),
                "source": item.get("source", ""),
                "sources": item.get("sources", []),
                "rerank_score": item.get("rerank_score"),
            }
            hits.append(
                Evidence(
                    doc_id=item.get("doc_id", ""),
                    text=item.get("text", "") or item.get("snippet", ""),
                    score=float(item.get("score", 0.0)),
                    source=item.get("source", "") or "http-search",
                    metadata=metadata,
                )
            )
        return SearchOutcome(
            hits=hits,
            confidence=raw.get("confidence", "low"),
            entities=list(raw.get("entities", [])),
            pool_size=int(raw.get("pool_size", len(hits))),
            timings_ms=dict(raw.get("timings_ms", {})),
        )

    def answer(
        self, question: str, top_k: int | None = None, session_id: str | None = None
    ) -> AnswerOutcome:
        payload: dict = {"question": question, "mode": "agent", "engine": "auto"}
        if session_id:
            payload["session_id"] = session_id
        if top_k:
            payload["overrides"] = {"top_k": top_k}
        raw = self._post("/api/v1/chat", payload)
        return AnswerOutcome(
            answer=raw.get("answer", ""),
            citations=list(raw.get("citations", [])),
            confidence=raw.get("confidence", "low"),
            entities=list(raw.get("entities", [])),
            engine=raw.get("engine", ""),
            agent_trace=list(raw.get("agent_trace", [])),
            agent_stop=raw.get("agent_stop", ""),
            usage=dict(raw.get("usage", {})),
            cost_yuan=float(raw.get("cost_yuan", 0.0)),
        )


__all__ = ["HttpKnowledgeBase"]
