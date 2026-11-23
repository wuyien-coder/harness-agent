"""OpenAI 兼容的 LLM 客户端（仅标准库，不绑定任何厂商 SDK）。

不读取、不引用原项目配置；密钥通过构造参数或 AGENT_HARNESS_LLM__* 环境变量提供。
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass


@dataclass
class LLMConfig:
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    timeout: float = 60.0
    max_tokens: int = 1600

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip())

    @classmethod
    def from_env(cls, prefix: str = "AGENT_HARNESS_LLM__") -> "LLMConfig":
        return cls(
            base_url=os.getenv(prefix + "BASE_URL", cls.base_url),
            api_key=os.getenv(prefix + "API_KEY", ""),
            model=os.getenv(prefix + "MODEL", cls.model),
            timeout=float(os.getenv(prefix + "TIMEOUT", str(cls.timeout))),
        )


class OpenAICompatClient:
    """最小可用的 chat/completions 客户端。"""

    name = "openai-compat"

    def __init__(self, config: LLMConfig, *, opener=None) -> None:
        self.config = config
        self._opener = opener or urllib.request.urlopen
        base = config.base_url.rstrip("/")
        self.endpoint = (
            base if base.endswith("/chat/completions") else base + "/chat/completions"
        )

    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        if not self.config.configured:
            raise RuntimeError("未配置大模型 API key")
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
            "stream": False,
        }
        try:
            request = urllib.request.Request(
                self.endpoint,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + self.config.api_key.strip(),
                },
                method="POST",
            )
            with self._opener(request, timeout=self.config.timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
            return raw["choices"][0]["message"]["content"]
        except Exception as exc:
            # urllib 的异常文本可能包含 Authorization 头，必须先脱敏再向外抛
            message = str(exc).replace(self.config.api_key, "***") if self.config.api_key else str(exc)
            raise RuntimeError("大模型请求失败: " + message[:300]) from None

    def stream_chat(self, messages: list[dict], *, temperature: float = 0.2,
                    max_tokens: int | None = None):
        """OpenAI SSE 流式对话，逐个 yield 文本增量（不解析 usage 事件）。"""
        if not self.config.configured:
            raise RuntimeError("未配置大模型 API key")
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        key = self.config.api_key.strip()
        try:
            request = urllib.request.Request(
                self.endpoint,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + key,
                },
                method="POST",
            )
            with self._opener(request, timeout=self.config.timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        data = json.loads(chunk)
                        delta = data["choices"][0]["delta"].get("content") or ""
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                        continue
                    if delta:
                        yield delta
        except Exception as exc:
            message = str(exc).replace(key, "***") if key else str(exc)
            raise RuntimeError("大模型流式请求失败: " + message[:300]) from None


__all__ = ["LLMConfig", "OpenAICompatClient"]
