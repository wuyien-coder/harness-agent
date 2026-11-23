"""完全合成的离线知识库实现（演示与测试用）。

注意：这里的语料是虚构的示例数据，不读取、不复制原项目的任何资料或索引。
"""
from __future__ import annotations

import re

from ..contracts import AnswerOutcome, Evidence, SearchOutcome
from ..ports import KnowledgeBasePort

_DOCS = [
    {
        "doc_id": "demo-doc-1",
        "person": "华罗庚",
        "text": "华罗庚是中国解析数论学家，曾在清华大学工作，师从熊庆来，培养了陈景润、王元等学生。",
    },
    {
        "doc_id": "demo-doc-2",
        "person": "陈景润",
        "text": "陈景润从事哥德巴赫猜想研究，师从华罗庚，是解析数论方向的代表性学者。",
    },
    {
        "doc_id": "demo-doc-3",
        "person": "王元",
        "text": "王元研究数论与哥德巴赫猜想，师从华罗庚，长期在中国科学院工作。",
    },
    {
        "doc_id": "demo-doc-4",
        "person": "苏步青",
        "text": "苏步青是微分几何学家，籍贯浙江平阳，曾在浙江大学任教。",
    },
    {
        "doc_id": "demo-doc-5",
        "person": "熊庆来",
        "text": "熊庆来是数学家，籍贯云南弥勒，曾指导华罗庚，在函数论领域有贡献。",
    },
]

_PERSONS = {
    "华罗庚": {"field": "解析数论", "hometown": "江苏金坛"},
    "陈景润": {"field": "解析数论", "hometown": "福建福州"},
    "王元": {"field": "数论", "hometown": "浙江义乌"},
    "苏步青": {"field": "微分几何", "hometown": "浙江平阳"},
    "熊庆来": {"field": "函数论", "hometown": "云南弥勒"},
}

# (学生, 老师, 关系)，teacher 边方向为 学生 -> 老师
_EDGES = [
    ("陈景润", "华罗庚", "teacher"),
    ("王元", "华罗庚", "teacher"),
    ("华罗庚", "熊庆来", "teacher"),
]

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2}|[a-zA-Z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


class InMemoryKnowledgeBase(KnowledgeBasePort):
    capabilities = frozenset(
        {"search", "answer", "profile", "neighbors", "path", "find_people"}
    )

    def search(self, query: str, top_k: int = 5, person: str | None = None) -> SearchOutcome:
        tokens = _tokens(query)
        scored: list[tuple[int, Evidence]] = []
        for doc in _DOCS:
            if person and doc["person"] != person:
                continue
            score = sum(1 for token in tokens if token in doc["text"] or token in doc["person"])
            if score <= 0:
                continue
            scored.append(
                (
                    score,
                    Evidence(
                        doc_id=doc["doc_id"],
                        text=doc["text"],
                        score=float(score),
                        source="demo-text",
                        metadata={"person_name": doc["person"], "section_title": "演示资料"},
                    ),
                )
            )
        scored.sort(key=lambda pair: (-pair[0], pair[1].doc_id))
        hits = [item for _, item in scored[:top_k]]
        confidence = "high" if len(hits) >= 3 else "medium" if hits else "low"
        return SearchOutcome(
            hits=hits,
            confidence=confidence,
            entities=list(dict.fromkeys(h.metadata["person_name"] for h in hits)),
            pool_size=len(scored),
        )

    def answer(
        self, question: str, top_k: int | None = None, session_id: str | None = None
    ) -> AnswerOutcome:
        result = self.search(question, top_k=top_k or 3)
        if not result.hits:
            return AnswerOutcome(answer="演示资料中没有找到相关证据。", engine="inmemory-stub")
        lines = [f"[{i + 1}] {hit.text}" for i, hit in enumerate(result.hits)]
        citations = [
            {
                "index": i + 1,
                "chunk_id": hit.doc_id,
                "person": hit.metadata.get("person_name", ""),
                "snippet": hit.text[:120],
                "score": hit.score,
            }
            for i, hit in enumerate(result.hits)
        ]
        return AnswerOutcome(
            answer="依据演示资料：\n" + "\n".join(lines),
            citations=citations,
            confidence=result.confidence,
            entities=result.entities,
            engine="inmemory-stub",
            agent_stop="finished",
            usage={"calls": 0, "note": "本地合成实现，不调用外部模型"},
        )

    def person_names(self) -> list[str]:
        return list(_PERSONS)

    def profile(self, name: str) -> dict:
        info = _PERSONS.get(name)
        if info is None:
            return {"name": name, "found": False, "profile": ""}
        text = f"{name}：研究方向{info['field']}，籍贯{info['hometown']}。"
        return {"name": name, "found": True, "profile": text}

    def neighbors(
        self, name: str, relation: str | None = None, direction: str = "both"
    ) -> dict:
        rows = []
        for student, teacher, rel in _EDGES:
            if relation and rel != relation:
                continue
            if direction in {"out", "both"} and student == name:
                rows.append({"source": student, "target": teacher, "relation": rel,
                             "statement": f"{student}师从{teacher}"})
            if direction in {"in", "both"} and teacher == name:
                rows.append({"source": student, "target": teacher, "relation": rel,
                             "statement": f"{student}师从{teacher}"})
        return {
            "found": name in _PERSONS,
            "count": len(rows),
            "relations": rows[:40],
            "truncated": len(rows) > 40,
            "message": f"命中 {len(rows)} 条关系" if rows else "当前方向下无关系命中",
        }

    def path(self, source: str, target: str, max_hops: int = 4) -> dict:
        adjacency: dict[str, set[str]] = {name: set() for name in _PERSONS}
        for a, b, _rel in _EDGES:
            adjacency.setdefault(a, set()).add(b)
            adjacency.setdefault(b, set()).add(a)
        if source not in adjacency or target not in adjacency:
            return {"path": [], "within_hops": False, "max_hops": max_hops}
        queue: list[tuple[str, list[str]]] = [(source, [source])]
        visited = {source}
        found: list[str] = []
        while queue:
            current, path = queue.pop(0)
            if current == target:
                found = path
                break
            for nxt in sorted(adjacency[current]):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, [*path, nxt]))
        within = bool(found) and len(found) - 1 <= max_hops
        return {
            "path": found if within else [],
            "within_hops": within,
            "max_hops": max_hops,
            "semantics": "无向连接路径，不代表有向师承",
        }

    def find_people(
        self, keyword: str, attribute: str = "field", offset: int = 0, limit: int = 20
    ) -> dict:
        matches = [
            name for name, info in _PERSONS.items() if keyword in info.get(attribute, "")
        ]
        page = matches[offset : offset + limit]
        return {
            "total_matches": len(matches),
            "names": page,
            "offset": offset,
            "has_more": offset + len(page) < len(matches),
            "matching": "字符串匹配，不是语义分类",
        }


__all__ = ["InMemoryKnowledgeBase"]
