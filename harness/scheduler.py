"""确定性任务调度器：拓扑排序 + 依赖短路 + 预算控制 + 有限重试。

设计取舍：刻意不做并发。结构化输出、可重放、可审计优先于吞吐；
如需并发，可在不改动端口契约的前提下替换本模块。
"""
from __future__ import annotations

from .contracts import TaskResult, TaskSpec, TaskStatus, ToolCall
from .ports import RunContext
from .registry import ToolRegistry


class TaskScheduler:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    @staticmethod
    def topological_order(tasks: list[TaskSpec]) -> list[TaskSpec]:
        by_id: dict[str, TaskSpec] = {}
        for task in tasks:
            if task.id in by_id:
                raise ValueError(f"重复任务 id: {task.id}")
            by_id[task.id] = task
        for task in tasks:
            for dep in task.depends_on:
                if dep == task.id:
                    raise ValueError(f"任务 {task.id} 不能依赖自身")
                if dep not in by_id:
                    raise ValueError(f"任务 {task.id} 依赖未知任务: {dep}")

        visiting: set[str] = set()
        visited: set[str] = set()
        ordered: list[TaskSpec] = []

        def visit(task: TaskSpec) -> None:
            if task.id in visited:
                return
            if task.id in visiting:
                raise ValueError(f"任务依赖成环: {task.id}")
            visiting.add(task.id)
            for dep in task.depends_on:
                visit(by_id[dep])
            visiting.discard(task.id)
            visited.add(task.id)
            ordered.append(task)

        for task in tasks:
            visit(task)
        return ordered

    def run(self, tasks: list[TaskSpec], context: RunContext) -> list[TaskResult]:
        ordered = self.topological_order(tasks)
        results: dict[str, TaskResult] = {}

        for spec in ordered:
            spec.validate()
            started = context.clock()

            def skip(reason: str) -> None:
                results[spec.id] = TaskResult(
                    task_id=spec.id,
                    status=TaskStatus.SKIPPED,
                    error=reason,
                    duration_ms=round((context.clock() - started) * 1000, 2),
                )
                # 预算/截止时间导致的跳过是正常调度结果，任务状态里已完整记录；
                # 不应混入 errors（那代表真正的执行失败，前端会标红）。
                if "预算" not in reason and "截止时间" not in reason:
                    context.errors.append(f"{spec.id}: {reason}")

            if context.remaining_ms() <= 0:
                skip("运行截止时间已到")
                continue
            if context.tool_budget_used:
                skip("工具调用预算耗尽")
                continue
            if context.task_budget_used:
                skip("任务数预算耗尽")
                continue
            failed_deps = [
                dep for dep in spec.depends_on
                if results[dep].status in {TaskStatus.FAILED, TaskStatus.SKIPPED}
            ]
            if failed_deps:
                skip("依赖任务未成功: " + ", ".join(failed_deps))
                continue

            attempts = 0
            output: dict = {}
            evidence: list = []
            calls: list[ToolCall] = []
            last_error = ""
            while attempts < spec.max_attempts:
                if context.remaining_ms() <= 0 or context.tool_budget_used:
                    last_error = last_error or "预算耗尽，重试中止"
                    break
                attempts += 1
                call = ToolCall(name=spec.tool, arguments=spec.arguments, task_id=spec.id)
                calls.append(call)
                context.tool_calls += 1
                result = self.registry.invoke(call, context)
                if result.ok:
                    output = dict(result.observation)
                    evidence = list(result.evidence)
                    last_error = ""
                    break
                last_error = result.error
                context.errors.append(f"{spec.id}: {result.error}")

            context.tasks_run += 1
            results[spec.id] = TaskResult(
                task_id=spec.id,
                status=TaskStatus.SUCCEEDED if not last_error else TaskStatus.FAILED,
                attempts=attempts,
                output=output,
                evidence=evidence,
                error=last_error,
                duration_ms=round((context.clock() - started) * 1000, 2),
                tool_calls=calls,
            )

        # 按调用方给出的顺序返回，便于结果回放对齐
        return [results[spec.id] for spec in tasks]


__all__ = ["TaskScheduler"]
