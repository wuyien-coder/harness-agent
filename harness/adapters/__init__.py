"""可替换适配器集合。

默认首选 NativeKnowledgeBase（直连副本内原项目能力，在 harness/native_kb.py）；
InMemoryKnowledgeBase / HttpKnowledgeBase 仅作为演示与降级选项保留。
"""
from .http_kb import HttpKnowledgeBase
from .inmemory_kb import InMemoryKnowledgeBase
from .llm_agent import LLMPlanner, LLMResponder, parse_json_object
from .llm_client import LLMConfig, OpenAICompatClient
from .planners import ExtractiveResponder, RulePlanner, ScriptedPlanner

__all__ = [
    "InMemoryKnowledgeBase",
    "HttpKnowledgeBase",
    "RulePlanner",
    "ScriptedPlanner",
    "ExtractiveResponder",
    "LLMConfig",
    "OpenAICompatClient",
    "LLMPlanner",
    "LLMResponder",
    "parse_json_object",
]
