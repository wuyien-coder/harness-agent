"""录制 harness 面板交互素材：关键状态截图 + 逐帧序列（供 ffmpeg 合成视频）。

设计取舍：
- 起一个本地 mock 服务器：服务真实面板 HTML，并按节奏逐帧推送预设 SSE 事件。
- Playwright 驱动真实 Chrome 访问，浏览器里跑的是真实前端代码，画面与线上一致。
- 用预设数据而非真实 LLM：零费用、可重复、帧节奏可控。

产物：
    docs/assets/screenshots/*.png   关键状态截图
    docs/assets/frames/frame-*.png  视频帧序列
    docs/assets/frames-manifest.json 帧→字幕映射

用法：
    python scripts/record_demo.py
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"
FRAMES = ASSETS / "frames"
SHOTS = ASSETS / "screenshots"
PANEL_HTML = ROOT / "harness" / "panel" / "index.html"

QUESTION = "华罗庚在数论方面有哪些贡献？"

ANSWER = (
    "华罗庚是中国现代数学的奠基人之一，在数论领域做出了世界级的贡献[1]。\n\n"
    "**1. 解析数论方面**\n\n"
    "他在三角和估计方面取得了开创性成果，给出了华林问题中指数和的非平凡估计，"
    "这一方法被国际上称为「华罗庚方法」，至今仍是解析数论的重要工具[2]。\n\n"
    "**2. 华—王方法与高维数值积分**\n\n"
    "他与王元合作，把数论方法应用于近似分析，建立了数值积分的「华—王方法」，"
    "在高维数值积分中给出了优于蒙特卡罗方法的求积公式[3]。\n\n"
    "**3. 对后世的影响**\n\n"
    "他的工作直接影响了陈景润等后辈数学家，推动了中国数论学派的形成与发展[4]。"
)

CITATIONS = [
    {"index": 1, "person": "华罗庚", "source": "本地文档 · 数学家传记", "chunk_id": "doc:0012",
     "section": "第一章 · 生平", "snippet": "华罗庚（1910—1985），中国著名数学家，被誉为「中国现代数学之父」，在解析数论、矩阵几何等领域有开创性贡献。"},
    {"index": 2, "person": "华罗庚方法", "source": "本地文档 · 数论专题", "chunk_id": "doc:0087",
     "section": "第三章 · 解析数论", "snippet": "华罗庚在三角和估计方面给出非平凡估计，形成著名的「华罗庚方法」，用于解决华林问题。"},
    {"index": 3, "person": "王元", "source": "本地文档 · 数论专题", "chunk_id": "doc:0091",
     "section": "第四章 · 应用", "snippet": "华罗庚与王元提出「华—王方法」，将数论方法应用于高维数值积分，优于传统蒙特卡罗方法。"},
    {"index": 4, "person": "陈景润", "source": "人物关系表", "chunk_id": "graph:0042",
     "section": "人物关系", "snippet": "华罗庚培养了陈景润、王元等一批数学家，推动中国数论学派形成。"},
]

AGENT_TRACE = [
    {"tool": "kb_search", "arguments": {"query": "华罗庚 数论 贡献"}, "status": "ok",
     "result_summary": "检索到 12 篇相关文档", "duration_ms": 620},
    {"tool": "kb_neighbors", "arguments": {"person": "华罗庚"}, "status": "ok",
     "result_summary": "找到 5 个关联人物节点", "duration_ms": 180},
    {"tool": "kb_answer", "arguments": {"question": QUESTION}, "status": "ok",
     "result_summary": "生成带引用的回答", "duration_ms": 1020},
]

HARNESS_TRACE = [
    {"step": "plan", "detail": "规划 3 个子任务：检索 / 图谱 / 问答"},
    {"step": "dispatch", "detail": "分派 kb_search → kb_neighbors → kb_answer"},
    {"step": "observe", "detail": "收集 4 条证据，置信度 高"},
    {"step": "finish", "detail": "汇总回答，耗时 1.8s"},
]

TASKS = [
    {"id": "t1", "status": "succeeded", "tool": "kb_search", "attempts": 1},
    {"id": "t2", "status": "succeeded", "tool": "kb_neighbors", "attempts": 1},
    {"id": "t3", "status": "succeeded", "tool": "kb_answer", "attempts": 1},
]

TOOLS = ["kb_search", "kb_answer", "kb_profile", "kb_neighbors", "kb_path", "kb_find_people"]


def sse(name: str, data: dict) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def build_events() -> list[tuple[str, bytes, float]]:
    """(阶段标题, SSE 字节, 推送后等待秒数)。"""
    events: list[tuple[str, bytes, float]] = []
    events.append(("规划任务：检索 → 图谱 → 问答", sse("stage", {"label": "正在规划任务…"}), 0.9))
    events.append(("调度工具：kb_search", sse("stage", {"label": "调用 kb_search 检索知识库…"}), 0.9))

    step = 12
    for i in range(0, len(ANSWER), step):
        chunk = ANSWER[i:i + step]
        events.append(("流式生成回答", sse("token", {"text": chunk}), 0.12))

    done = {
        "status": "completed", "answer": ANSWER, "confidence": "high", "engine": "sequential",
        "citations": CITATIONS, "agent_trace": AGENT_TRACE, "agent_stop": "finished",
        "cost_yuan": 0.0523, "timings_ms": {"total": 1826},
        "tasks": TASKS, "trace": HARNESS_TRACE,
        "usage": {"prompt_tokens": 1240, "completion_tokens": 386},
        "available_tools": TOOLS,
    }
    events.append(("完成：引用 + 成本 + 调度轨迹", sse("done", done), 1.2))
    return events


class DemoHandler(BaseHTTPRequestHandler):
    events = build_events()

    def log_message(self, *args):
        pass

    def _send_html(self):
        body = PANEL_HTML.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send_html()
        elif self.path.startswith("/api/health"):
            self._send_json({"mode": "native", "llm_enabled": True, "llm_model": "deepseek-v4-pro",
                             "runs": 128, "kb_docs": 326, "kb_chunks": 9921})
        elif self.path.startswith("/api/tools"):
            self._send_json({"tools": TOOLS})
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path == "/api/config":
            self._send_json({"ok": True, "mode": "native", "llm_enabled": True, "llm_model": "deepseek-v4-pro"})
            return
        if self.path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for _, payload, wait in self.events:
                    self.wfile.write(payload)
                    self.wfile.flush()
                    time.sleep(wait)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        self.send_error(404)


def start_server() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def record(headless: bool = True) -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)
    server, port = start_server()
    base_url = f"http://127.0.0.1:{port}/"
    events = DemoHandler.events
    manifest: list[dict] = []
    frame_no = 0

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=headless,
                executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                args=["--no-first-run"],
            )
            page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page.goto(base_url, wait_until="networkidle")
            page.wait_for_timeout(500)

            # 欢迎页
            page.screenshot(path=str(SHOTS / "01-welcome.png"))
            page.screenshot(path=str(FRAMES / f"frame-{frame_no:03d}.png"))
            manifest.append({"frame": f"frame-{frame_no:03d}.png", "label": "Agent Harness 问答面板"})
            frame_no += 1

            # 输入问题
            page.fill("#question", QUESTION)
            page.wait_for_timeout(400)
            page.screenshot(path=str(SHOTS / "02-question.png"))
            page.screenshot(path=str(FRAMES / f"frame-{frame_no:03d}.png"))
            manifest.append({"frame": f"frame-{frame_no:03d}.png", "label": "输入问题：" + QUESTION})
            frame_no += 1

            # 点击发送 → 逐阶段截图
            page.click("#sendBtn")
            last_label = ""
            for i, (label, _, wait) in enumerate(events):
                page.wait_for_timeout(int(wait * 1000))
                # 每 3 个 token 帧截一次，避免帧数过多
                is_token = label == "流式生成回答"
                if is_token and i % 3 != 0:
                    continue
                page.screenshot(path=str(FRAMES / f"frame-{frame_no:03d}.png"))
                if label != last_label:
                    manifest.append({"frame": f"frame-{frame_no:03d}.png", "label": label})
                    last_label = label
                frame_no += 1

            # 等完成态稳定
            page.wait_for_selector(".harness-trace", timeout=10000)
            page.wait_for_timeout(500)
            page.screenshot(path=str(SHOTS / "03-answer.png"), full_page=True)
            page.screenshot(path=str(FRAMES / f"frame-{frame_no:03d}.png"))
            manifest.append({"frame": f"frame-{frame_no:03d}.png", "label": "回答完成：引用卡片与成本"})
            frame_no += 1

            # 展开调度轨迹
            try:
                page.click(".harness-trace summary")
                page.wait_for_timeout(300)
            except Exception:
                pass
            page.screenshot(path=str(SHOTS / "04-trace.png"), full_page=True)
            page.screenshot(path=str(FRAMES / f"frame-{frame_no:03d}.png"))
            manifest.append({"frame": f"frame-{frame_no:03d}.png", "label": "调度轨迹：plan → dispatch → observe → finish"})
            frame_no += 1

            # 引用切换
            jump = page.locator(".jump-evidence").first
            if jump.count():
                jump.click()
                page.wait_for_timeout(500)
                page.screenshot(path=str(SHOTS / "05-citations.png"), full_page=True)
                page.screenshot(path=str(FRAMES / f"frame-{frame_no:03d}.png"))
                manifest.append({"frame": f"frame-{frame_no:03d}.png", "label": "点击引用：右侧证据联动"})
                frame_no += 1

            browser.close()
    finally:
        server.shutdown()

    (ASSETS / "frames-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"截图目录: {SHOTS}")
    print(f"帧序列: {FRAMES} ({frame_no} 帧)")
    print(f"字幕清单: {ASSETS / 'frames-manifest.json'}")


if __name__ == "__main__":
    record(headless=True)
