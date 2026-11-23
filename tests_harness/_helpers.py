"""测试公共辅助：全部为内存对象，无文件读写、无网络、无真实模型调用。"""
from __future__ import annotations

from harness.contracts import AgentRequest, Evidence, ToolResult
from harness.ports import RunContext, ToolPort


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_context(allowed=(), *, request=None, clock=None):
    request = request or AgentRequest(goal="test")
    clock = clock or FakeClock()
    return RunContext(
        request=request,
        allowed_tools=frozenset(allowed),
        deadline=clock() + 10,
        clock=clock,
    )


class EchoTool(ToolPort):
    name = "echo"
    description = "回显文本"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 50}},
        "required": ["text"],
        "additionalProperties": False,
    }

    def invoke(self, arguments, context):
        return ToolResult(
            ok=True,
            observation={"echo": arguments["text"]},
            evidence=[Evidence(doc_id="echo-1", text=arguments["text"], source="test")],
        )


class BoomTool(ToolPort):
    name = "boom"
    description = "总是抛异常"
    parameters = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}

    def invoke(self, arguments, context):
        raise RuntimeError("boom")


class CountingTool(ToolPort):
    def __init__(self, name, log, *, fail_times=0, side_effect=None):
        self.name = name
        self.description = "记录调用顺序"
        self.parameters = {
            "type": "object", "properties": {}, "required": [], "additionalProperties": False
        }
        self.log = log
        self.fail_times = fail_times
        self.side_effect = side_effect
        self.calls = 0

    def invoke(self, arguments, context):
        self.calls += 1
        self.log.append(self.name)
        if self.side_effect:
            self.side_effect()
        if self.calls <= self.fail_times:
            return ToolResult(ok=False, error="临时失败")
        return ToolResult(ok=True, observation={"tool": self.name})
