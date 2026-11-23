"""Agent Harness —— 在原项目副本之上新增的 agent 编排层。

与 src/mathkb（照抄的原项目）严格分层：本包只通过 bootstrap 装配的 NativeRuntime
调用原项目能力，不改其源码/配置/数据/运行方式。
"""
from .contracts import AgentRequest, AgentResponse, Evidence, RunStatus
from .native_kb import NativeKnowledgeBase
from .runner import Harness
from .scheduler import TaskScheduler

__version__ = "0.2.0"

__all__ = [
    "AgentRequest",
    "AgentResponse",
    "Evidence",
    "RunStatus",
    "NativeKnowledgeBase",
    "Harness",
    "TaskScheduler",
]
