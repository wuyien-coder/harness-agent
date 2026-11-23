"""Harness 顶层入口：装配原项目能力 + 编排端口 + 可选落盘。

默认链路（推荐）：
  NativeKnowledgeBase（直连 src/mathkb 的真实检索/图谱/问答）
  + LLMPlanner / LLMResponder（如有 API key）
  + 规则规划 / 摘录回答（无 key 时的零成本降级）

密钥来源：仅进程环境变量 MATHKB_LLM__API_KEY 或构造时显式传入的 llm 客户端，
不读取、也不写入副本的 .env 文件。
"""
from __future__ import annotations

import argparse
import os

from .adapters import (
    ExtractiveResponder,
    HttpKnowledgeBase,
    InMemoryKnowledgeBase,
    LLMConfig,
    LLMPlanner,
    LLMResponder,
    OpenAICompatClient,
    RulePlanner,
)
from .bootstrap import NativeRuntime, create_runtime
from .contracts import AgentRequest, AgentResponse
from .loop import AgentLoop
from .native_kb import NativeKnowledgeBase
from .ports import KnowledgeBasePort, PlannerPort, ResponderPort
from .registry import ToolRegistry
from .scheduler import TaskScheduler
from .storage import InMemoryRunStore
from .tools import build_kb_tools


def llm_config_from_client(llm) -> dict | None:
    """把 harness 的 LLM 客户端映射为 bootstrap 需要的原项目 provider 配置。"""
    config = getattr(llm, "config", None)
    if config is None:
        return None
    return {
        "provider": "openai",
        "base_url": getattr(config, "base_url", ""),
        "model": getattr(config, "model", ""),
        "api_key": getattr(config, "api_key", ""),
    }


def build_llm_from_env() -> OpenAICompatClient | None:
    """从环境变量构造真实大模型客户端；未配置 key 时返回 None（走零成本降级）。"""
    key = os.getenv("MATHKB_LLM__API_KEY", "").strip()
    if not key:
        return None
    config = LLMConfig(
        base_url=os.getenv("MATHKB_LLM__BASE_URL", "https://api.deepseek.com/v1").strip(),
        api_key=key,
        model=os.getenv("MATHKB_LLM__MODEL", "deepseek-chat").strip(),
        timeout=float(os.getenv("MATHKB_LLM__TIMEOUT", "60")),
    )
    return OpenAICompatClient(config)


class Harness:
    def __init__(
        self,
        kb: KnowledgeBasePort,
        *,
        planner: PlannerPort | None = None,
        responder: ResponderPort | None = None,
        include_graph: bool = True,
        store=None,
        llm=None,
        runtime: NativeRuntime | None = None,
    ) -> None:
        self.registry = ToolRegistry()
        for tool in build_kb_tools(kb, include_graph=include_graph):
            self.registry.register(tool)
        self.scheduler = TaskScheduler(self.registry)
        self.llm = llm
        self.runtime = runtime
        if llm is not None:
            planner = planner or LLMPlanner(llm, self.registry.describe())
            responder = responder or LLMResponder(llm)
        self.planner = planner or RulePlanner(self.registry.names(), kb.person_names())
        self.responder = responder or ExtractiveResponder()
        self.loop = AgentLoop(self.planner, self.responder, self.scheduler)
        self.store = store if store is not None else InMemoryRunStore()

    @classmethod
    def native(cls, *, llm=None, store=None, runtime: NativeRuntime | None = None,
               include_graph: bool = True) -> "Harness":
        """用副本内原项目的真实能力构造 harness（推荐入口）。"""
        llm = llm if llm is not None else build_llm_from_env()
        # 有真实 LLM 客户端时，原生 ChatService 也走 openai provider，
        # 这样 kb_answer 的回答与流式 token 都来自同一个上游模型。
        rt = runtime or create_runtime(llm_config=llm_config_from_client(llm))
        kb = NativeKnowledgeBase(rt)
        return cls(kb, llm=llm, store=store, runtime=rt, include_graph=include_graph)

    @property
    def tools(self) -> list[str]:
        return self.registry.names()

    def run(self, request: AgentRequest | str) -> AgentResponse:
        if isinstance(request, str):
            request = AgentRequest(goal=request)
        if request.tool_allowlist is not None:
            unknown = [name for name in request.tool_allowlist if name not in self.registry.names()]
            if unknown:
                raise ValueError("请求包含未注册工具: " + ", ".join(unknown))
        response = self.loop.run(request)
        self.store.save(response)
        return response

    def close(self) -> None:
        if self.runtime is not None:
            self.runtime.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Agent Harness（默认直连副本内原项目真实能力；可选离线演示 / HTTP）"
    )
    parser.add_argument("--goal", required=True, help="本次任务目标")
    parser.add_argument("--mode", choices=["native", "offline", "http"], default="native",
                        help="native=直连原项目能力（默认）")
    parser.add_argument("--base-url", default=None, help="http 模式下的服务 base URL")
    parser.add_argument("--allowlist", nargs="*", default=None, help="本次允许调用的工具名")
    parser.add_argument("--max-steps", type=int, default=6)
    parser.add_argument("--max-tool-calls", type=int, default=12)
    parser.add_argument("--json", action="store_true", help="输出完整 JSON 契约")
    args = parser.parse_args(argv)

    harness: Harness
    if args.mode == "native":
        harness = Harness.native()
    elif args.mode == "http":
        if not args.base_url:
            parser.error("http 模式必须提供 --base-url")
        harness = Harness(HttpKnowledgeBase(args.base_url))
    else:
        harness = Harness(InMemoryKnowledgeBase())
    try:
        response = harness.run(AgentRequest(
            goal=args.goal,
            tool_allowlist=args.allowlist,
            max_steps=args.max_steps,
            max_tool_calls=args.max_tool_calls,
        ))
    finally:
        harness.close()

    if args.json:
        print(response.to_json())
    else:
        print(f"状态: {response.status.value}")
        print(f"请求: {response.request_id}")
        print(f"回答:\n{response.answer or '(无)'}")
        print(f"证据: {len(response.evidence)} 条 | 工具调用: {response.tool_calls_count} 次")
        print(f"工具: {', '.join(harness.tools)}")
        if response.errors:
            print("错误: " + " | ".join(response.errors[:5]))
    return 0 if response.status.value in {"completed", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
