"""运行期流式事件通道（进程内、按请求隔离）。

设计取舍：
- 只加一个 ContextVar 事件汇聚点，不改动 AgentRequest / TaskSpec / ToolResult 等端口契约；
- 任何 emit 失败都被吞掉，流式通道绝不影响原有同步问答链路；
- 没注册 sink 时 emit 是零成本空操作，CLI / 测试行为完全不变。
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable

Sink = Callable[[dict[str, Any]], None]

_SINK: ContextVar[Sink | None] = ContextVar("agent_harness_stream_sink", default=None)


def has_sink() -> bool:
    return _SINK.get() is not None


def emit(event: str, **data: Any) -> None:
    """向当前请求的流式通道推送一个事件；无通道或通道异常时静默跳过。"""
    sink = _SINK.get()
    if sink is None:
        return
    try:
        sink({"event": event, **data})
    except Exception:
        pass


class stream_sink:
    """在 with 块内把 sink 绑定到当前线程/协程上下文。"""
    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self._token = None

    def __enter__(self) -> "stream_sink":
        self._token = _SINK.set(self.sink)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            _SINK.reset(self._token)


__all__ = ["emit", "has_sink", "stream_sink", "Sink"]
