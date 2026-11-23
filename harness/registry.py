"""工具注册表：工具发现、白名单、参数校验、异常隔离的唯一入口。"""
from __future__ import annotations

from .contracts import ToolCall, ToolResult
from .ports import RunContext, ToolPort
from .stream_events import emit


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolPort] = {}

    def register(self, tool: ToolPort, *, replace: bool = False) -> None:
        if not isinstance(tool, ToolPort):
            raise TypeError("tool 必须是 ToolPort 实例")
        if not tool.name:
            raise ValueError("tool.name 不能为空")
        if tool.name in self._tools and not replace:
            raise ValueError(f"工具已注册: {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolPort | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self) -> list[dict]:
        return [
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
            for tool in self._tools.values()
        ]

    def invoke(self, call: ToolCall, context: RunContext) -> ToolResult:
        """所有工具调用都经过这里；任何失败都被降级为 ok=False 的观察结果。"""
        started = context.clock()

        def fail(message: str) -> ToolResult:
            return ToolResult(
                ok=False,
                error=message[:600],
                duration_ms=round((context.clock() - started) * 1000, 2),
            )

        tool = self._tools.get(call.name)
        if tool is None:
            return fail(f"未注册工具: {call.name}")
        if call.name not in context.allowed_tools:
            return fail(f"工具不在本次 allowlist 中: {call.name}")
        if context.remaining_ms() <= 0:
            return fail("运行截止时间已到，工具未执行")
        error = tool.validate(call.arguments)
        if error:
            return fail(error)
        if call.name != "kb_answer":  # kb_answer 自己会推更细的检索/生成阶段
            emit("stage", label=f"调用工具：{call.name}", tool=call.name)
        try:
            result = tool.invoke(call.arguments, context)
        except Exception as exc:  # 工具异常绝不击穿 harness
            return fail(f"{type(exc).__name__}: {exc}")
        if not isinstance(result, ToolResult):
            return fail(f"工具 {call.name} 返回类型错误: {type(result).__name__}")
        result.duration_ms = round((context.clock() - started) * 1000, 2)
        if call.name != "kb_answer":
            emit("stage", label=f"工具完成：{call.name}", tool=call.name)
        return result


__all__ = ["ToolRegistry"]
