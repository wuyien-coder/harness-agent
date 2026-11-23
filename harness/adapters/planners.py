"""内置规划器与回答器（均为确定性、离线、无 SDK 依赖）。

它们只是默认实现；Harness 允许注入任何 PlannerPort / ResponderPort。
"""
from __future__ import annotations

from ..contracts import PlanDecision, TaskSpec
from ..ports import PlannerPort, ResponderPort

_GRAPH_HINTS = ("老师", "学生", "师承", "导师", "关系", "路径", "图谱", "师生")


class ScriptedPlanner(PlannerPort):
    """按预设决策序列返回，供测试与回放使用。"""

    name = "scripted"

    def __init__(self, decisions: list[PlanDecision]) -> None:
        self._decisions = list(decisions)
        self._cursor = 0

    def plan(self, request, observations: list[dict], step: int) -> PlanDecision:
        if self._cursor >= len(self._decisions):
            return PlanDecision(action="finish", rationale="脚本已耗尽")
        decision = self._decisions[self._cursor]
        self._cursor += 1
        return decision


class RulePlanner(PlannerPort):
    """基于关键词与已注册工具名的轻量规划器。"""

    name = "rule"

    def __init__(
        self, tool_names: list[str] | None = None, known_names: list[str] | None = None
    ) -> None:
        self.tool_names = set(tool_names or [])
        self.known_names = list(known_names or [])

    def plan(self, request, observations: list[dict], step: int) -> PlanDecision:
        if observations:
            return PlanDecision(action="finish", rationale="已获得工具观察，进入回答阶段")

        tasks: list[TaskSpec] = []
        if "kb_answer" in self.tool_names:
            tasks.append(
                TaskSpec(id="t_answer", tool="kb_answer",
                         arguments={"question": request.goal}, label="知识问答")
            )
        if "kb_search" in self.tool_names:
            tasks.append(
                TaskSpec(id="t_search", tool="kb_search",
                         arguments={"query": request.goal, "top_k": 5}, label="证据检索")
            )
        if not tasks:
            if not self.tool_names:
                return PlanDecision(action="abort", rationale="没有注册任何可用工具")
            tool = sorted(self.tool_names)[0]
            tasks = [TaskSpec(id="t_only", tool=tool, arguments={}, label="唯一可用工具")]

        graph_banned = any(
            "不用图谱" in item or "禁用图谱" in item or "不查图谱" in item
            for item in request.constraints
        )
        if not graph_banned and "kb_neighbors" in self.tool_names:
            if any(hint in request.goal for hint in _GRAPH_HINTS):
                name = next((n for n in self.known_names if n in request.goal), None)
                if name:
                    tasks.append(
                        TaskSpec(id="t_neighbors", tool="kb_neighbors",
                                 arguments={"name": name, "direction": "both"},
                                 label="关系补充")
                    )
        return PlanDecision(
            action="dispatch", tasks=tasks,
            rationale="首轮并行收集问答结果与检索证据",
        )


class ExtractiveResponder(ResponderPort):
    """优先采纳问答端口结果，否则把证据拼成带编号的摘录。"""

    name = "extractive"

    def respond(self, request, evidence: list, observations: list[dict]) -> str:
        primary = [item for item in evidence if item.source.startswith("kb_answer") and item.text]
        if primary:
            best = primary[-1]
            answer = best.text
            citations = best.metadata.get("citations") or []
            if citations:
                refs = "; ".join(
                    f"[{c.get('index', i + 1)}] {c.get('person') or c.get('chunk_id', '')}"
                    for i, c in enumerate(citations[:5])
                )
                answer += "\n\n引用：" + refs
            return answer[:4000]

        if not evidence:
            return ""
        lines = [f"[{i}] {item.text}" for i, item in enumerate(evidence[:5], 1)]
        return "依据已有证据：\n" + "\n".join(lines)[:4000]


__all__ = ["ScriptedPlanner", "RulePlanner", "ExtractiveResponder"]
