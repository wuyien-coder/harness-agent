"""运行记录存储：默认不落盘；显式启用时也只允许写在本项目目录内。"""
from __future__ import annotations

from pathlib import Path

from .contracts import AgentResponse

HARNESS_ROOT = Path(__file__).resolve().parents[1]


class InMemoryRunStore:
    def __init__(self) -> None:
        self.responses: list[AgentResponse] = []

    def save(self, response: AgentResponse) -> None:
        self.responses.append(response)


class FileRunStore:
    def __init__(self, directory: str | Path = "runs") -> None:
        target = Path(directory)
        if not target.is_absolute():
            target = HARNESS_ROOT / target
        target = target.resolve()
        try:
            target.relative_to(HARNESS_ROOT)
        except ValueError as exc:
            raise ValueError("运行记录目录必须位于 agent-harness 项目目录内") from exc
        self.directory = target

    def save(self, response: AgentResponse) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        safe_id = response.request_id.replace("/", "_").replace("\\", "_")
        path = self.directory / f"{safe_id}.json"
        path.write_text(response.to_json(), encoding="utf-8")
        return path


__all__ = ["InMemoryRunStore", "FileRunStore", "HARNESS_ROOT"]
