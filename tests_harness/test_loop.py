import unittest

from harness.adapters import ExtractiveResponder, ScriptedPlanner
from harness.contracts import (
    AgentRequest,
    PlanDecision,
    RunStatus,
    TaskSpec,
    TaskStatus,
)
from harness.loop import AgentLoop
from harness.registry import ToolRegistry
from harness.scheduler import TaskScheduler

from ._helpers import CountingTool, EchoTool, FakeClock


def build_loop(planner, tools=None, clock=None):
    registry = ToolRegistry()
    for tool in tools or [EchoTool()]:
        registry.register(tool)
    return AgentLoop(
        planner, ExtractiveResponder(), TaskScheduler(registry),
        clock=clock or FakeClock(),
    )


class LoopTests(unittest.TestCase):
    def test_planner_answer_short_circuits(self):
        planner = ScriptedPlanner([PlanDecision(action="finish", answer="直接答案")])
        response = build_loop(planner).run(AgentRequest(goal="g"))
        self.assertEqual(response.status, RunStatus.COMPLETED)
        self.assertEqual(response.answer, "直接答案")
        self.assertEqual(response.tool_calls_count, 0)
        self.assertEqual([entry.kind for entry in response.trace], ["plan", "finish"])

    def test_dispatch_then_finish_builds_grounded_answer(self):
        planner = ScriptedPlanner([
            PlanDecision(action="dispatch", tasks=[
                TaskSpec(id="t1", tool="echo", arguments={"text": "你好"})
            ]),
            PlanDecision(action="finish"),
        ])
        response = build_loop(planner).run(AgentRequest(goal="g"))
        self.assertEqual(response.status, RunStatus.COMPLETED)
        self.assertIn("[1] 你好", response.answer)
        self.assertEqual(response.tool_calls_count, 1)
        self.assertEqual(response.tasks[0].status, TaskStatus.SUCCEEDED)
        self.assertEqual(response.evidence[0].doc_id, "echo-1")
        self.assertEqual(response.usage["planner_calls"], 2)
        self.assertIn("total", response.timings_ms)

    def test_invalid_tool_is_reported_and_does_not_crash(self):
        planner = ScriptedPlanner([
            PlanDecision(action="dispatch", tasks=[
                TaskSpec(id="t1", tool="unknown", arguments={})
            ]),
            PlanDecision(action="finish"),
        ])
        response = build_loop(planner).run(AgentRequest(goal="g"))
        self.assertEqual(response.status, RunStatus.PARTIAL)
        self.assertFalse(response.answer)
        self.assertEqual(response.tasks[0].status, TaskStatus.FAILED)
        self.assertTrue(any("未注册工具" in err for err in response.errors))

    def test_budget_exhaustion_status(self):
        planner = ScriptedPlanner([
            PlanDecision(action="dispatch", tasks=[
                TaskSpec(id="t1", tool="echo", arguments={"text": "a"})
            ]),
            PlanDecision(action="dispatch", tasks=[
                TaskSpec(id="t2", tool="echo", arguments={"text": "b"})
            ]),
        ])
        response = build_loop(planner).run(
            AgentRequest(goal="g", max_steps=2, max_tool_calls=1)
        )
        self.assertEqual(response.status, RunStatus.BUDGET_EXHAUSTED)
        # 预算用尽后不再规划：没有注定被跳过的任务，已有证据仍生成回答。
        self.assertEqual(len(response.tasks), 1)
        self.assertEqual(response.tasks[0].status, TaskStatus.SUCCEEDED)
        self.assertTrue(response.answer)
        self.assertEqual(response.usage["planner_calls"], 1)

    def test_abort_with_evidence_still_answers(self):
        planner = ScriptedPlanner([
            PlanDecision(action="dispatch", tasks=[
                TaskSpec(id="t1", tool="echo", arguments={"text": "你好"})
            ]),
            PlanDecision(action="abort", rationale="模型请求中止"),
        ])
        response = build_loop(planner).run(AgentRequest(goal="g"))
        self.assertEqual(response.status, RunStatus.PARTIAL)
        self.assertIn("[1] 你好", response.answer)

    def test_deadline_stops_loop_and_keeps_evidence(self):
        clock = FakeClock()
        slow_tool = CountingTool("slow", [], side_effect=lambda: clock.advance(20))
        planner = ScriptedPlanner([
            PlanDecision(action="dispatch", tasks=[TaskSpec(id="t1", tool="slow")]),
            PlanDecision(action="finish", answer="不该到达"),
        ])
        response = build_loop(planner, tools=[slow_tool], clock=clock).run(
            AgentRequest(goal="g", timeout_ms=8_000, max_steps=3)
        )
        self.assertEqual(response.status, RunStatus.BUDGET_EXHAUSTED)
        self.assertNotEqual(response.answer, "不该到达")
        self.assertTrue(response.trace[-1].name == "deadline")

    def test_allowlist_is_enforced_in_loop(self):
        planner = ScriptedPlanner([
            PlanDecision(action="dispatch", tasks=[
                TaskSpec(id="t1", tool="echo", arguments={"text": "a"})
            ]),
            PlanDecision(action="finish"),
        ])
        response = build_loop(planner).run(
            AgentRequest(goal="g", tool_allowlist=["something-else"])
        )
        self.assertEqual(response.tasks[0].status, TaskStatus.FAILED)
        self.assertTrue(any("allowlist" in err for err in response.errors))


if __name__ == "__main__":
    unittest.main()
