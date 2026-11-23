"""Agent Harness 问答交互面板服务（本地标准库 HTTP 服务）。

默认数据源 = native（直连副本内原项目的真实检索/图谱/问答能力）；
可选 offline（内置合成语料，零成本演示）/ http（外部问答服务）。

启动（从副本根目录）：
  PYTHONPATH=src:. D:/code/python.exe -m harness.panel.server --port 8765

真实大模型：启动时从进程环境变量 MATHKB_LLM__API_KEY 注入（不落盘、不回显），
也可在面板里临时填写（仅保存在服务端进程内存）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

PANEL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PANEL_DIR.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from harness import AgentRequest, Harness  # noqa: E402
from harness.adapters import (  # noqa: E402
    HttpKnowledgeBase,
    InMemoryKnowledgeBase,
    LLMConfig,
    OpenAICompatClient,
)
from harness.bootstrap import create_runtime  # noqa: E402
from harness.stream_events import stream_sink  # noqa: E402

STATE: dict = {
    "mode": "native",
    "base_url": "",
    "llm_enabled": False,
    "llm_model": "",
    "index_stats": {},
    "runs": 0,
    "lock": threading.RLock(),
}


def extract_presentation(response: dict) -> dict:
    """把 native 问答证据里嵌的 citation / 置信度 / ReAct 轨迹提到响应顶层。

    借鉴原项目前端的呈现契约：原面板从 ChatAnswer 拿 citations / confidence /
    agent_trace / agent_stop / engine；harness 把这些放在 kb_answer 这条证据的
    metadata 里。这里做一次归一化，方便前端直接渲染，又不改变原有字段（向后兼容）。

    离线 / HTTP 模式下没有 kb_answer 证据时返回空结构，前端自动降级。
    """
    empty = {
        "citations": [], "confidence": "", "engine": "",
        "agent_trace": [], "agent_stop": "", "cost_yuan": 0.0,
    }
    for evidence in response.get("evidence", []) or []:
        metadata = evidence.get("metadata") or {}
        if "citations" not in metadata and "agent_trace" not in metadata:
            continue
        source = str(evidence.get("source") or "")
        return {
            "citations": list(metadata.get("citations") or []),
            "confidence": str(metadata.get("confidence") or ""),
            "engine": source.split("/", 1)[1] if "/" in source else source,
            "agent_trace": list(metadata.get("agent_trace") or []),
            "agent_stop": str(metadata.get("agent_stop") or ""),
            "cost_yuan": float(metadata.get("cost_yuan") or 0.0),
        }
    return empty


def sse_event(event: dict) -> bytes:
    """把内部事件序列化成一帧 SSE；纯函数，便于单测。"""
    name = str(event.get("event") or "message")
    # done / error 用 {"event":..., "data": {...}} 构造；token / stage 直接平铺字段。
    # 统一序列化为一层 payload，前端 onEvent(name, data) 拿到的数据结构才一致。
    data = event.get("data") if "data" in event else {
        k: v for k, v in event.items() if k != "event"
    }
    return (
        f"event: {name}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    ).encode("utf-8")


def make_llm(payload: dict | None):
    """从面板输入或环境变量构造 LLM 客户端；密钥绝不回显或落盘。"""
    if not payload or not payload.get("enabled"):
        return None
    api_key = payload.get("api_key") or os.getenv("MATHKB_LLM__API_KEY", "")
    base_url = payload.get("base_url") or os.getenv(
        "MATHKB_LLM__BASE_URL", "https://api.deepseek.com/v1"
    )
    model = payload.get("model") or os.getenv("MATHKB_LLM__MODEL", "deepseek-chat")
    config = LLMConfig(base_url=base_url, api_key=api_key, model=model,
                       timeout=float(os.getenv("MATHKB_LLM__TIMEOUT", "120")))
    if not config.configured:
        raise ValueError("未配置大模型 API key：请在面板填写，或设置环境变量 MATHKB_LLM__API_KEY")
    return OpenAICompatClient(config)


def build_harness(mode: str, base_url: str, llm_payload: dict | None = None) -> Harness:
    """构建 harness。native 模式复用同一份原生运行时，避免重复建索引。"""
    llm = make_llm(llm_payload)
    if mode == "native":
        previous = STATE.get("harness")
        signature = (
            ":".join([llm.config.base_url, llm.config.model, "with-key"]) if llm else "offline"
        )
        runtime = getattr(previous, "runtime", None)
        # LLM 配置变了必须重建原生运行时：ChatService 的 provider 也随密钥切换，
        # 否则会出现"面板显示接了大模型，实际检索回答仍是 stub"的错配。
        if runtime is None or STATE.get("runtime_signature") != signature:
            if runtime is not None:
                runtime.close()
            llm_cfg = ({"provider": "openai", "base_url": llm.config.base_url,
                        "model": llm.config.model, "api_key": llm.config.api_key}
                       if llm else None)
            runtime = create_runtime(llm_config=llm_cfg)
            with STATE["lock"]:
                STATE["runtime_signature"] = signature
        harness = Harness.native(llm=llm, runtime=runtime)
        with STATE["lock"]:
            STATE["index_stats"] = dict(runtime.index_stats)
        return harness
    if mode == "http":
        if not base_url.strip():
            raise ValueError("接入外部问答服务时必须填写 base_url")
        return Harness(HttpKnowledgeBase(base_url.strip()), llm=llm)
    # offline
    return Harness(InMemoryKnowledgeBase(), llm=llm)


class PanelHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body, content_type: str = "application/json; charset=utf-8") -> None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = bytes(body)
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("请求体为空")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if path in {"/", "/index.html"}:
                html = (PANEL_DIR / "index.html").read_text(encoding="utf-8")
                return self._send(200, html, "text/html; charset=utf-8")
            if path == "/api/health":
                return self._send(200, {
                    "ok": True,
                    "mode": STATE["mode"],
                    "base_url": STATE["base_url"],
                    "runs": STATE["runs"],
                    "llm_enabled": STATE["llm_enabled"],
                    "llm_model": STATE["llm_model"],
                    "index_stats": STATE["index_stats"],
                })
            if path == "/api/tools":
                return self._send(200, {"tools": STATE["harness"].tools})
            self._send(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            payload = self._json_body()
            if path == "/api/config":
                mode = payload.get("mode", "native")
                base_url = payload.get("base_url", "")
                if mode not in {"native", "offline", "http"}:
                    raise ValueError("mode 只能是 native / offline / http")
                harness = build_harness(mode, base_url, payload.get("llm"))
                llm = harness.llm
                with STATE["lock"]:
                    old = STATE.get("harness")
                    STATE["harness"] = harness
                    STATE["mode"] = mode
                    STATE["base_url"] = base_url if mode == "http" else ""
                    STATE["llm_enabled"] = llm is not None
                    STATE["llm_model"] = llm.config.model if llm else ""
                if old is not None and old is not harness and mode != "native":
                    old.close()
                return self._send(200, {
                    "ok": True, "mode": mode, "tools": harness.tools,
                    "llm_enabled": STATE["llm_enabled"], "llm_model": STATE["llm_model"],
                    "index_stats": STATE["index_stats"],
                })

            if path == "/api/stream":
                question = payload.get("question", "")
                if not isinstance(question, str) or not question.strip():
                    raise ValueError("问题不能为空")
                harness = STATE["harness"]
                request = AgentRequest(
                    goal=question.strip(),
                    tool_allowlist=payload.get("allowlist") or None,
                    max_steps=int(payload.get("max_steps", 6)),
                    max_tool_calls=int(payload.get("max_tool_calls", 12)),
                    timeout_ms=int(payload.get("timeout_ms", 90000)),
                )
                # SSE 用连接关闭标识结束，不能带 Content-Length。
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self.wfile.flush()
                self.close_connection = True

                def sink(event: dict) -> None:
                    try:
                        self.wfile.write(sse_event(event))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass

                try:
                    with stream_sink(sink):
                        response = harness.run(request)
                    with STATE["lock"]:
                        STATE["runs"] += 1
                    out = response.to_dict()
                    sink({
                        "event": "done",
                        "data": {
                            **out,
                            "available_tools": harness.tools,
                            **extract_presentation(out),
                        },
                    })
                except Exception as exc:  # noqa: BLE001
                    message = str(exc)
                    llm = getattr(harness, "llm", None)
                    key = getattr(getattr(llm, "config", None), "api_key", "")
                    if key:
                        message = message.replace(key, "***")
                    sink({"event": "error", "data": {
                        "message": f"{type(exc).__name__}: {message[:300]}"
                    }})
                return

            if path == "/api/ask":
                question = payload.get("question", "")
                if not isinstance(question, str) or not question.strip():
                    raise ValueError("问题不能为空")
                harness = STATE["harness"]
                request = AgentRequest(
                    goal=question.strip(),
                    tool_allowlist=payload.get("allowlist") or None,
                    max_steps=int(payload.get("max_steps", 6)),
                    max_tool_calls=int(payload.get("max_tool_calls", 12)),
                    timeout_ms=int(payload.get("timeout_ms", 90000)),
                )
                response = harness.run(request)
                with STATE["lock"]:
                    STATE["runs"] += 1
                payload_out = response.to_dict()
                return self._send(200, {
                    **payload_out,
                    "available_tools": harness.tools,
                    **extract_presentation(payload_out),
                })

            self._send(404, {"error": "not found"})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent Harness 问答交互面板")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--mode", choices=["native", "offline", "http"], default="native")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--llm-enabled", action="store_true", help="启动时启用大模型（需环境变量 key）")
    parser.add_argument("--llm-base-url", default="")
    parser.add_argument("--llm-model", default="")
    args = parser.parse_args()

    llm_payload = {
        "enabled": args.llm_enabled,
        "base_url": args.llm_base_url,
        "model": args.llm_model,
        "api_key": "",
    }
    harness = build_harness(args.mode, args.base_url, llm_payload)
    llm = harness.llm
    with STATE["lock"]:
        STATE["harness"] = harness
        STATE["mode"] = args.mode
        STATE["base_url"] = args.base_url if args.mode == "http" else ""
        STATE["llm_enabled"] = llm is not None
        STATE["llm_model"] = llm.config.model if llm else ""

    server = ThreadingHTTPServer((args.host, args.port), PanelHandler)
    print(f"问答交互面板已启动: http://{args.host}:{args.port}")
    print(f"数据源: {args.mode}" + (f" ({args.base_url})" if args.base_url else ""))
    if args.mode == "native":
        stats = STATE["index_stats"]
        print(f"原项目索引: {stats.get('chunks')} 块 / {stats.get('persons')} 人物 / {stats.get('relations')} 关系")
    print("大模型: " + (STATE["llm_model"] if STATE["llm_enabled"] else "未启用（规则规划 + 摘录回答）"))
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        harness.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
