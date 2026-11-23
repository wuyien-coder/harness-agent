import unittest

from harness.contracts import AgentRequest, TaskSpec, TaskStatus
from harness.ports import RunContext
from harness.registry import ToolRegistry
from harness.scheduler import TaskScheduler

from ._helpers import CountingTool, FakeClock


class SchedulerTests(unittest.TestCase):
    def make_scheduler(self, log, **kwargs):
        registry = ToolRegistry()
        registry.register(CountingTool("produce", log, **kwargs))
        registry.register(CountingTool("consume", log, **kwargs))
        registry.register(CountingTool("bad", log, fail_times=99))
        return TaskScheduler(registry)

    def make_context(self, max_tool_calls=12):
        clock = FakeClock()
        request = AgentRequest(goal="g", max_tool_calls=max_tool_calls, max_tasks=8)
        return RunContext(
            request=request, allowed_tools=frozenset({"produce", "consume", "bad"}),
            deadline=clock() + 10, clock=clock,
        )

    def test_topological_order(self):
        log = []
        scheduler = self.make_scheduler(log)
        tasks = [
            TaskSpec(id="b", tool="consume", depends_on=["a"]),
            TaskSpec(id="a", tool="produce"),
        ]
        results = scheduler.run(tasks, self.make_context())
        self.assertEqual(log, ["produce", "consume"])
        self.assertEqual([r.task_id for r in results], ["b", "a"])
        self.assertTrue(all(r.status == TaskStatus.SUCCEEDED for r in results))

    def test_cycle_and_unknown_dependency_rejected(self):
        scheduler = self.make_scheduler([])
        with self.assertRaises(ValueError):
            scheduler.run(
                [TaskSpec(id="a", tool="produce", depends_on=["b"]),
                 TaskSpec(id="b", tool="consume", depends_on=["a"])],
                self.make_context(),
            )
        with self.assertRaises(ValueError):
            scheduler.run([TaskSpec(id="a", tool="produce", depends_on=["ghost"])],
                          self.make_context())

    def test_failed_dependency_skips_downstream(self):
        log = []
        scheduler = self.make_scheduler(log)
        results = scheduler.run(
            [TaskSpec(id="bad", tool="bad"),
             TaskSpec(id="after", tool="consume", depends_on=["bad"])],
            self.make_context(),
        )
        self.assertEqual(results[0].status, TaskStatus.FAILED)
        self.assertEqual(results[1].status, TaskStatus.SKIPPED)
        self.assertIn("依赖任务未成功", results[1].error)
        self.assertEqual(log, ["bad"])

    def test_retry_then_success(self):
        log = []
        scheduler = self.make_scheduler(log)
        # 仅让 produce 临时失败一次
        scheduler.registry._tools["produce"].fail_times = 1
        results = scheduler.run(
            [TaskSpec(id="a", tool="produce", max_attempts=2)], self.make_context()
        )
        self.assertEqual(results[0].status, TaskStatus.SUCCEEDED)
        self.assertEqual(results[0].attempts, 2)
        self.assertEqual(log, ["produce", "produce"])

    def test_tool_budget_skips_remaining_tasks(self):
        log = []
        scheduler = self.make_scheduler(log)
        context = self.make_context(max_tool_calls=1)
        results = scheduler.run(
            [TaskSpec(id="a", tool="produce"), TaskSpec(id="b", tool="consume")],
            context,
        )
        self.assertEqual(results[0].status, TaskStatus.SUCCEEDED)
        self.assertEqual(results[1].status, TaskStatus.SKIPPED)
        self.assertIn("预算耗尽", results[1].error)
        self.assertEqual(log, ["produce"])
        # 预算跳过是正常调度结果：记在任务状态里，不能混进 errors 被前端标红。
        self.assertEqual(context.errors, [])

    def test_failed_dependency_still_recorded_as_error(self):
        scheduler = self.make_scheduler([])
        context = self.make_context()
        scheduler.run(
            [TaskSpec(id="bad", tool="bad"),
             TaskSpec(id="after", tool="consume", depends_on=["bad"])],
            context,
        )
        self.assertTrue(any("bad" in err for err in context.errors))


if __name__ == "__main__":
    unittest.main()
