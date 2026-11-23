import unittest

from harness.contracts import ToolCall
from harness.registry import ToolRegistry

from ._helpers import BoomTool, EchoTool, make_context


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.register(EchoTool())
        self.registry.register(BoomTool())

    def test_register_duplicate_and_describe(self):
        with self.assertRaises(ValueError):
            self.registry.register(EchoTool())
        self.registry.register(EchoTool(), replace=True)
        self.assertEqual(self.registry.names(), ["boom", "echo"])
        self.assertEqual(
            sorted(item["name"] for item in self.registry.describe()), ["boom", "echo"]
        )

    def test_unknown_and_disallowed_tools_are_not_executed(self):
        context = make_context(allowed=["echo"])
        unknown = self.registry.invoke(ToolCall(name="nope"), context)
        self.assertFalse(unknown.ok)
        self.assertIn("未注册", unknown.error)
        disallowed = self.registry.invoke(ToolCall(name="boom"), context)
        self.assertFalse(disallowed.ok)
        self.assertIn("allowlist", disallowed.error)
        self.assertEqual(context.tool_calls, 0)

    def test_argument_validation(self):
        context = make_context(allowed=["echo"])
        missing = self.registry.invoke(ToolCall(name="echo", arguments={}), context)
        self.assertFalse(missing.ok)
        self.assertIn("缺少必填参数", missing.error)
        extra = self.registry.invoke(
            ToolCall(name="echo", arguments={"text": "hi", "unknown": 1}), context
        )
        self.assertFalse(extra.ok)
        self.assertIn("未知参数", extra.error)
        short = self.registry.invoke(
            ToolCall(name="echo", arguments={"text": ""}), context
        )
        self.assertFalse(short.ok)

    def test_success_and_exception_isolation(self):
        context = make_context(allowed=["echo", "boom"])
        good = self.registry.invoke(ToolCall(name="echo", arguments={"text": "hi"}), context)
        self.assertTrue(good.ok)
        self.assertEqual(good.observation["echo"], "hi")
        self.assertEqual(len(good.evidence), 1)
        self.assertGreaterEqual(good.duration_ms, 0.0)
        bad = self.registry.invoke(ToolCall(name="boom"), context)
        self.assertFalse(bad.ok)
        self.assertIn("RuntimeError: boom", bad.error)

    def test_deadline_blocks_execution(self):
        context = make_context(allowed=["echo"])
        context.deadline = context.clock() - 1
        result = self.registry.invoke(ToolCall(name="echo", arguments={"text": "hi"}), context)
        self.assertFalse(result.ok)
        self.assertIn("截止时间", result.error)


if __name__ == "__main__":
    unittest.main()
