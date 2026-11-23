"""Start/stop the complete local stack without registering system services."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
STATE = ROOT / ".runtime" / "state" / "application.json"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def child_environment():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONUTF8"] = "1"
    env["HF_HUB_OFFLINE"] = "1"
    env["TOKENIZERS_PARALLELISM"] = "false"
    env["OMP_NUM_THREADS"] = "4"
    # Full mode reads its local .env. Offline launcher settings must not leak in.
    for name in list(env):
        if name.startswith("MATHKB_"):
            env.pop(name)
    return env


def fetch_json(url):
    with OPENER.open(url, timeout=3) as response:
        return json.load(response)


def save_state(data):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.is_file() else {}


def owned_process(record):
    try:
        process = psutil.Process(int(record["pid"]))
        if abs(process.create_time() - float(record["created"])) > 1:
            return None
        if Path(process.cwd()).resolve() != ROOT:
            return None
        return process
    except (psutil.Error, KeyError, ValueError):
        return None


def stop_record(record):
    process = owned_process(record)
    if not process:
        return
    children = process.children(recursive=True)
    for item in reversed(children):
        try:
            item.terminate()
        except psutil.Error:
            pass
    try:
        process.terminate()
    except psutil.Error:
        pass
    _, alive = psutil.wait_procs([*children, process], timeout=10)
    for item in alive:
        try:
            item.kill()
        except psutil.Error:
            pass


def stop_previous_demo():
    path = ROOT / ".index" / "demo-process.json"
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if Path(data.get("project", "")).resolve() != ROOT:
        return
    created = datetime.fromisoformat(data["started_at"]).timestamp()
    stop_record({"pid": data["launcher_pid"], "created": created})
    path.unlink(missing_ok=True)


def spawn(component, arguments, env):
    logs = ROOT / ".runtime" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    output = logs / f"{component}.log"
    with output.open("a", encoding="utf-8") as logfile:
        process = subprocess.Popen([str(PYTHON), *arguments], cwd=ROOT, env=env,
            stdout=logfile, stderr=logfile,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return {"pid": process.pid, "created": psutil.Process(process.pid).create_time(),
            "log": str(output.relative_to(ROOT))}


def start(open_browser=True):
    if not (ROOT / ".env").is_file():
        raise RuntimeError("完整配置尚未生成，请先完成prepare_full与本地组件准备")
    from mathkb.core.config import Settings
    settings = Settings()
    if settings.embedding.provider != "bge" or settings.llm.provider != "openai":
        raise RuntimeError("当前.env并非完整模式，需要BGE与真实模型服务")
    base = f"http://127.0.0.1:{settings.server.port}"
    current = load_state()
    if owned_process(current.get("api", {})) and owned_process(current.get("worker", {})):
        try:
            health = fetch_json(base + "/health")
            if health.get("status") == "ok" and health.get("llm_provider") == "openai":
                print("完整版本已运行：" + base + "/web/", flush=True)
                if open_browser:
                    webbrowser.open(base + "/web/")
                return
        except Exception:
            pass
    print("[1/4] 启动本地 PostgreSQL 与 Redis…", flush=True)
    infrastructure = ROOT / "scripts" / "start_infrastructure.ps1"
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(infrastructure)],
                   cwd=ROOT, check=True, timeout=180)
    env = child_environment()
    print("[2/4] 检查数据库结构…", flush=True)
    subprocess.run([str(PYTHON), "-m", "alembic", "upgrade", "head"], cwd=ROOT,
                   env=env, check=True, timeout=120)
    stop_previous_demo()
    for record in current.values():
        if isinstance(record, dict):
            stop_record(record)
    print("[3/4] 加载全量知识库与BGE索引，首次启动需稍候…", flush=True)
    state = {"port": settings.server.port}
    state["worker"] = spawn("worker", ["-m", "celery", "-A", "mathkb.worker:celery_app", "worker",
                            "--pool=threads", "--concurrency=1", "--loglevel=INFO", "--hostname=mathknow@%h"], env)
    save_state(state)
    state["api"] = spawn("api", ["-m", "uvicorn", "mathkb.main:app", "--host", "127.0.0.1",
                                      "--port", str(settings.server.port)], env)
    save_state(state)
    for attempt in range(600):
        if not owned_process(state["api"]):
            raise RuntimeError("API启动失败，请查看.runtime/logs/api.log")
        try:
            health = fetch_json(base + "/health")
            if health.get("status") == "ok":
                break
        except Exception:
            pass
        if attempt % 20 == 19:
            print("  正在准备索引，启动日志：.runtime/logs/api.log", flush=True)
        time.sleep(1)
    else:
        raise RuntimeError("知识库启动等待超时，请查看.runtime/logs/api.log")
    print("[4/4] 检查数据库、缓存和任务队列…", flush=True)
    subprocess.run([str(PYTHON), "scripts/check_stack.py", "--require-all"], cwd=ROOT,
                   env=env, check=True, timeout=30)
    print("完整版本已就绪：" + base + "/web/", flush=True)
    print(f"索引 {health.get('chunks')} 块 / 图谱 {health.get('persons')} 人物 / {health.get('embedder')}", flush=True)
    if open_browser:
        webbrowser.open(base + "/web/")


def stop():
    state = load_state()
    for name in ("api", "worker"):
        stop_record(state.get(name, {}))
    save_state({})
    script = ROOT / "scripts" / "stop_infrastructure.ps1"
    if script.is_file():
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                       cwd=ROOT, check=True, timeout=60)
    print("新版完整服务已停止，原项目8000端口不受影响。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "restart", "stop", "status"])
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "restart":
            current = load_state()
            for name in ("api", "worker"):
                stop_record(current.get(name, {}))
            save_state({})
            start(not args.no_browser)
        elif args.action == "start":
            start(not args.no_browser)
        elif args.action == "stop":
            stop()
        else:
            state = load_state()
            for name in ("api", "worker"):
                print(name, "running" if owned_process(state.get(name, {})) else "stopped")
            port = state.get("port", 8001)
            print(f"http://127.0.0.1:{port}/web/")
        return 0
    except Exception as exc:
        # Provider credentials/connection strings must not appear in launcher errors.
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print("启动管理失败：" + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
