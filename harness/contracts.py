"""Agent harness 的输入 / 输出数据契约。

刻意使用纯标准库 dataclass 定义，不 import 任何原知识问答项目的类型，
因此本模块的序列化结构与原项目解耦（字段含义参考其公开 API 契约）。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class RunStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


def jsonable(value: Any) -> Any:
    """把 dataclass / Enum 递归转成可 json.dumps 的结构。"""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return jsonable(asdict(value))
    return value


@dataclass
class Evidence:
    """一条可引用的证据，与原项目的 Hit/Citation 概念对齐但不共享类型。"""

    doc_id: str
    text: str
    score: float = 0.0
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return jsonable(self)


@dataclass
class SearchOutcome:
    """知识库检索端口的返回结构。"""

    hits: list[Evidence] = field(default_factory=list)
    confidence: str = "low"
    entities: list[str] = field(default_factory=list)
    pool_size: int = 0
    timings_ms: dict[str, float] = field(default_factory=dict)


@dataclass
class AnswerOutcome:
    """知识库问答端口的返回结构（字段子集对齐 ChatAnswer.to_dict）。"""

    answer: str
    citations: list[dict] = field(default_factory=list)
    confidence: str = "low"
    entities: list[str] = field(default_factory=list)
    engine: str = ""
    agent_trace: list[dict] = field(default_factory=list)
    agent_stop: str = ""
    usage: dict = field(default_factory=dict)
    cost_yuan: float = 0.0


# --------------------------------------------------------------------------- #
# 输入
# --------------------------------------------------------------------------- #
@dataclass
class AgentRequest:
    goal: str
    constraints: list[str] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    tool_allowlist: list[str] | None = None  # None 表示不额外限制
    max_tasks: int = 8
    max_tool_calls: int = 12
    max_steps: int = 6
    timeout_ms: int = 8_000
    request_id: str = ""

    def __post_init__(self) -> None:
        if not self.request_id:
            self.request_id = "req-" + uuid.uuid4().hex[:12]
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise ValueError("goal 不能为空")
        if len(self.goal) > 1000:
            raise ValueError("goal 长度不能超过 1000")
        limits = (
            ("max_tasks", self.max_tasks, 1, 32),
            ("max_tool_calls", self.max_tool_calls, 1, 64),
            ("max_steps", self.max_steps, 1, 12),
            ("timeout_ms", self.timeout_ms, 100, 120_000),
        )
        for name, value, low, high in limits:
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name} 必须是 {low}~{high} 的整数")
        if self.tool_allowlist is not None:
            if not isinstance(self.tool_allowlist, list) or not all(
                isinstance(item, str) and item for item in self.tool_allowlist
            ):
                raise ValueError("tool_allowlist 必须是非空字符串列表或 None")

    def to_dict(self) -> dict:
        return jsonable(self)


@dataclass
class TaskSpec:
    """一个可被调度的最小工作单元。"""

    id: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    max_attempts: int = 1
    label: str = ""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("TaskSpec.id 不能为空")
        if not isinstance(self.tool, str) or not self.tool:
            raise ValueError("TaskSpec.tool 不能为空")
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or not 1 <= self.max_attempts <= 3:
            raise ValueError("TaskSpec.max_attempts 必须是 1~3 的整数")
        if self.id in self.depends_on:
            raise ValueError("任务不能依赖自身")

    def to_dict(self) -> dict:
        return jsonable(self)


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    task_id: str = ""

    def to_dict(self) -> dict:
        return jsonable(self)


@dataclass
class PlanDecision:
    """规划器输出：派发任务 / 结束 / 中止。"""

    action: str  # dispatch | finish | abort
    tasks: list[TaskSpec] = field(default_factory=list)
    answer: str = ""
    rationale: str = ""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.action not in {"dispatch", "finish", "abort"}:
            raise ValueError(f"未知规划动作: {self.action}")
        if self.action == "dispatch" and not self.tasks:
            raise ValueError("dispatch 决策必须带至少一个任务")
        for task in self.tasks:
            task.validate()

    def to_dict(self) -> dict:
        return jsonable(self)


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
@dataclass
class ToolResult:
    ok: bool
    observation: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    error: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return jsonable(self)


@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    attempts: int = 0
    output: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    error: str = ""
    duration_ms: float = 0.0
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_dict(self) -> dict:
        return jsonable(self)


@dataclass
class TraceEntry:
    step: int
    kind: str  # plan | dispatch | observe | finish
    name: str
    status: str
    duration_ms: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return jsonable(self)


@dataclass
class AgentResponse:
    request_id: str
    goal: str
    status: RunStatus
    answer: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    tasks: list[TaskResult] = field(default_factory=list)
    trace: list[TraceEntry] = field(default_factory=list)
    tool_calls_count: int = 0
    errors: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return jsonable(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
