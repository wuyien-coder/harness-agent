import json
import unittest

from harness.contracts import (
    AgentRequest,
    AgentResponse,
    Evidence,
    PlanDecision,
    RunStatus,
    TaskSpec,
    TaskStatus,
)


class ContractTests(unittest.TestCase):
    def test_request_defaults_and_validation(self):
        request = AgentRequest(goal="查询关系")
        self.assertTrue(request.request_id.startswith("req-"))
        self.assertIsNone(request.tool_allowlist)
        for bad in ("", "   ", 123):
            with self.assertRaises(ValueError):
                AgentRequest(goal=bad)
        with self.assertRaises(ValueError):
            AgentRequest(goal="ok", max_steps=0)
        with self.assertRaises(ValueError):
            AgentRequest(goal="ok", max_tool_calls=99)
        with self.assertRaises(ValueError):
            AgentRequest(goal="ok", timeout_ms=10)
        with self.assertRaises(ValueError):
            AgentRequest(goal="ok", tool_allowlist=[1, 2])

    def test_task_spec_validation(self):
        with self.assertRaises(ValueError):
            TaskSpec(id="", tool="echo")
        with self.assertRaises(ValueError):
            TaskSpec(id="t", tool="echo", max_attempts=5)
        with self.assertRaises(ValueError):
            TaskSpec(id="t", tool="echo", depends_on=["t"])
        with self.assertRaises(ValueError):
            PlanDecision(action="jump")
        with self.assertRaises(ValueError):
            PlanDecision(action="dispatch", tasks=[])

    def test_response_is_json_serializable_with_enum_values(self):
        response = AgentResponse(
            request_id="r1", goal="g", status=RunStatus.COMPLETED,
            answer="a", evidence=[Evidence("d1", "text")],
            tasks=[], trace=[], usage={"tool_calls": 0},
        )
        data = json.loads(response.to_json())
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["evidence"][0]["doc_id"], "d1")
        self.assertEqual(TaskStatus.SUCCEEDED.value, "succeeded")


if __name__ == "__main__":
    unittest.main()
