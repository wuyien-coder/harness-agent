"""流式事件通道测试：请求隔离、静默降级、异常不击穿主链路。"""
from __future__ import annotations

import unittest

from harness.stream_events import emit, has_sink, stream_sink


class StreamEventsTests(unittest.TestCase):
    def test_no_sink_is_noop(self):
        self.assertFalse(has_sink())
        emit("token", text="不影响同步链路")

    def test_events_reach_sink_and_are_isolated(self):
        first, second = [], []
        with stream_sink(first.append):
            self.assertTrue(has_sink())
            emit("stage", label="规划中")
            emit("token", text="你")
            with stream_sink(second.append):
                emit("token", text="其他请求")
            emit("token", text="好")
        self.assertFalse(has_sink())
        self.assertEqual([e["event"] for e in first], ["stage", "token", "token"])
        self.assertEqual([e["text"] for e in first if e["event"] == "token"], ["你", "好"])
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["text"], "其他请求")

    def test_sink_exception_is_swallowed(self):
        def broken(_event):
            raise RuntimeError("客户端断开")

        with stream_sink(broken):
            emit("token", text="仍然继续")
            self.assertTrue(has_sink())


if __name__ == "__main__":
    unittest.main()
