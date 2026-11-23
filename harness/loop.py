"""Agent 主循环：plan -> dispatch -> observe -> ... -> finish。

循环只做编排，不含任何领域知识；每一步都写入 trace，所有失败都降级为可观察结果。
"""
from __future__ import annotations

import time

from .contracts import (
    AgentRequest,
    AgentResponse,
    Evidence,
    RunStatus,
    TaskResult,
    TraceEntry,
)
from .ports import PlannerPort, ResponderPort, RunContext
from .scheduler import TaskScheduler
from .stream_events import emit


class AgentLoop:
    def __init__(
        self,
        planner: PlannerPort,
        responder: ResponderPort,
        scheduler: TaskScheduler,
        *,
        clock=time.monotonic,
    ) -> None:
        self.planner = planner
        self.responder = responder
        self.scheduler = scheduler
        self.clock = clock

    def run(self, request: AgentRequest) -> AgentResponse:
        request.validate()
        started = self.clock()
        deadline = started + request.timeout_ms / 1000
        allowed = (
            frozenset(request.tool_allowlist)
            if request.tool_allowlist is not None
            else frozenset(self.scheduler.registry.names())
        )
        context = RunContext(
            request=request,
            allowed_tools=allowed,
            deadline=deadline,
            clock=self.clock,
        )

        evidence: list[Evidence] = []
        seen_doc_ids: set[str] = set()
        observations: list[dict] = []
        trace: list[TraceEntry] = []
        task_results: list[TaskResult] = []
        answer = ""
        plan_calls = 0
        reason = "steps"

        for step in range(1, request.max_steps + 1):
            if context.remaining_ms() <= 0:
                reason = "deadline"
                break
            # 预算已用尽时不再调用规划器：既省一次模型调用，也避免产生
            # 必然被跳过的"计划任务"污染审计轨迹。已有证据交给回答阶段收尾。
            if step > 1 and (context.tool_budget_used or context.task_budget_used):
                reason = "budget"
                break

            plan_started = self.clock()
            emit("stage", label=f"正在规划任务（第 {step} 轮）")
            try:
                decision = self.planner.plan(request, list(observations), step)
                decision.validate()
            except Exception as exc:
                context.errors.append(f"planner: {type(exc).__name__}: {exc}")
                trace.append(
                    TraceEntry(
                        step=step, kind="plan", name=self.planner.name, status="error",
                        duration_ms=round((self.clock() - plan_started) * 1000, 2),
                        detail={"error": str(exc)[:600]},
                    )
                )
                reason = "error"
                break
            plan_calls += 1
            trace.append(
                TraceEntry(
                    step=step, kind="plan", name=self.planner.name, status=decision.action,
                    duration_ms=round((self.clock() - plan_started) * 1000, 2),
                    detail={"rationale": decision.rationale,
                            "tasks": [task.to_dict() for task in decision.tasks]},
                )
            )

            if decision.action == "abort":
                reason = "abort"
                break
            if decision.action == "finish":
                answer = decision.answer or self.responder.respond(request, evidence, observations)
                reason = "finish"
                break

            dispatch_started = self.clock()
            results = self.scheduler.run(decision.tasks[: request.max_tasks], context)
            task_results.extend(results)
            trace.append(
                TraceEntry(
                    step=step, kind="dispatch", name=f"{len(results)} tasks", status="ok",
                    duration_ms=round((self.clock() - dispatch_started) * 1000, 2),
                    detail={"statuses": [result.status.value for result in results]},
                )
            )
            for result in results:
                observations.append(
                    {
                        "task_id": result.task_id,
                        "status": result.status.value,
                        "output": result.output,
                        "error": result.error,
                    }
                )
                if result.status.value == "failed":
                    context.errors.append(f"{result.task_id}: {result.error}")
                for item in result.evidence:
                    if item.doc_id not in seen_doc_ids:
                        seen_doc_ids.add(item.doc_id)
                        evidence.append(item)
            trace.append(
                TraceEntry(
                    step=step, kind="observe", name="merge", status="ok",
                    detail={"evidence_count": len(evidence), "observations": len(observations)},
                )
            )
        else:
            reason = "steps"

        if reason == "finish":
            status = RunStatus.COMPLETED if answer else RunStatus.PARTIAL
        elif reason in {"abort", "error"}:
            # 规划器中止/出错时若已有证据，仍要给出基于证据的回答，而不是空白答案。
            if evidence and not answer:
                answer = self.responder.respond(request, evidence, observations)
            status = RunStatus.PARTIAL if answer else RunStatus.FAILED
        else:
            if evidence and not answer:
                answer = self.responder.respond(request, evidence, observations)
            if reason == "deadline" or context.tool_budget_used or context.task_budget_used:
                status = RunStatus.BUDGET_EXHAUSTED
            else:
                status = RunStatus.PARTIAL if evidence else RunStatus.FAILED

        trace.append(
            TraceEntry(step=len(trace), kind="finish", name=reason, status=status.value)
        )

        return AgentResponse(
            request_id=request.request_id,
            goal=request.goal,
            status=status,
            answer=answer,
            evidence=evidence,
            tasks=task_results,
            trace=trace,
            tool_calls_count=context.tool_calls,
            errors=list(context.errors),
            timings_ms={"total": round((self.clock() - started) * 1000, 2)},
            usage={
                "planner_calls": plan_calls,
                "tool_calls": context.tool_calls,
                "tasks": context.tasks_run,
            },
        )


__all__ = ["AgentLoop"]
