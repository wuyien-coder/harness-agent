"""边界端口（Port）定义。

harness 只依赖这些抽象，不依赖任何具体实现，也不 import 原项目。
原知识问答能力通过 KnowledgeBasePort 的 HTTP 实现接入（见 adapters/http_kb.py）。
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from .contracts import (
    AgentRequest,
    AnswerOutcome,
    PlanDecision,
    RunStatus,
    SearchOutcome,
    ToolResult,
)


class PortError(Exception):
    """端口层基础异常。"""


class UnsupportedCapabilityError(PortError):
    """知识库适配器不具备该结构化能力。"""


_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate_arguments(parameters: dict, arguments: dict) -> str | None:
    """校验 JSON Schema 的一个实用子集；返回错误信息或 None。"""
    if not isinstance(arguments, dict):
        return "arguments 必须是 JSON 对象"
    props = parameters.get("properties", {}) or {}
    required = parameters.get("required", []) or []
    for key in required:
        if key not in arguments:
            return f"缺少必填参数: {key}"
    if parameters.get("additionalProperties", True) is False:
        extra = [key for key in arguments if key not in props]
        if extra:
            return "未知参数: " + ", ".join(sorted(extra))
    for key, spec in props.items():
        if key not in arguments:
            continue
        value = arguments[key]
        expected = spec.get("type")
        if expected:
            python_type = _TYPE_MAP.get(expected)
            if python_type is not None:
                if expected in {"integer", "number"} and isinstance(value, bool):
                    return f"{key} 类型错误: 需要 {expected}"
                if not isinstance(value, python_type):
                    return f"{key} 类型错误: 需要 {expected}"
        if "enum" in spec and value not in spec["enum"]:
            return f"{key} 取值不在允许范围"
        if isinstance(value, str):
            if "minLength" in spec and len(value) < spec["minLength"]:
                return f"{key} 太短"
            if "maxLength" in spec and len(value) > spec["maxLength"]:
                return f"{key} 太长"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in spec and value < spec["minimum"]:
                return f"{key} 小于最小值"
            if "maximum" in spec and value > spec["maximum"]:
                return f"{key} 大于最大值"
    return None


class ToolPort(ABC):
    """工具边界：只读、参数可校验、异常一律转成 ToolResult。"""

    name: str = ""
    description: str = ""
    parameters: dict = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    @abstractmethod
    def invoke(self, arguments: dict, context: "RunContext") -> ToolResult:
        ...

    def validate(self, arguments: dict) -> str | None:
        return validate_arguments(self.parameters, arguments)


@dataclass
class RunContext:
    """单次运行的共享上下文：预算、时钟、允许调用的工具集合。"""

    request: AgentRequest
    allowed_tools: frozenset[str]
    deadline: float
    clock: Callable[[], float] = time.monotonic
    tool_calls: int = 0
    tasks_run: int = 0
    errors: list[str] = field(default_factory=list)

    def remaining_ms(self) -> float:
        return max(0.0, (self.deadline - self.clock()) * 1000)

    @property
    def tool_budget_used(self) -> bool:
        return self.tool_calls >= self.request.max_tool_calls

    @property
    def task_budget_used(self) -> bool:
        return self.tasks_run >= self.request.max_tasks


class KnowledgeBasePort(ABC):
    """知识库边界。

    capabilities 决定 harness 会注册哪些结构化工具（最小能力 = search + answer）。
    """

    capabilities: frozenset[str] = frozenset({"search", "answer"})

    @abstractmethod
    def search(self, query: str, top_k: int = 5, person: str | None = None) -> SearchOutcome:
        ...

    @abstractmethod
    def answer(
        self, question: str, top_k: int | None = None, session_id: str | None = None
    ) -> AnswerOutcome:
        ...

    def person_names(self) -> list[str]:
        return []

    def profile(self, name: str) -> dict:
        raise UnsupportedCapabilityError("profile 能力不可用")

    def neighbors(
        self, name: str, relation: str | None = None, direction: str = "both"
    ) -> dict:
        raise UnsupportedCapabilityError("neighbors 能力不可用")

    def path(self, source: str, target: str, max_hops: int = 4) -> dict:
        raise UnsupportedCapabilityError("path 能力不可用")

    def find_people(
        self, keyword: str, attribute: str = "field", offset: int = 0, limit: int = 20
    ) -> dict:
        raise UnsupportedCapabilityError("find_people 能力不可用")


class PlannerPort(ABC):
    """任务规划边界（不绑定任何具体大模型 SDK）。"""

    name: str = "base"

    @abstractmethod
    def plan(self, request: AgentRequest, observations: list[dict], step: int) -> PlanDecision:
        ...


class ResponderPort(ABC):
    """最终回答生成边界。"""

    name: str = "base"

    @abstractmethod
    def respond(self, request: AgentRequest, evidence: list, observations: list[dict]) -> str:
        ...


__all__ = [
    "PortError",
    "UnsupportedCapabilityError",
    "validate_arguments",
    "ToolPort",
    "RunContext",
    "KnowledgeBasePort",
    "PlannerPort",
    "ResponderPort",
    "RunStatus",
]
