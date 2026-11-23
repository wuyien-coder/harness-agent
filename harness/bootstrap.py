"""原项目运行时装配器（bootstrap）。

职责：在**副本自身**的目录里装配 mathkb 的 AppContext / ChatService，
让 harness 的 NativeKnowledgeBase 可以直接调用原项目的真实检索、图谱与问答能力。

隔离原则：
- 只 import 副本内的 src/mathkb，不改其源码、配置、数据或运行方式；
- `get_settings()` 默认读取副本的 `.env`（ROOT 指向副本自身）；
- 所有覆盖项通过 `apply_offline_env()` 写入**当前进程的环境变量**，不落盘；
- 云数据库连接串 / 密钥默认不写死：如副本 .env 含 PostgreSQL URL，
  `apply_offline_env()` 会把它替换为副本内的 SQLite 索引库，避免依赖外部服务；
- LLM 密钥仅从调用方传入或环境变量读取，绝不写入文件或日志。

注意：`apply_offline_env()` 必须在 `get_settings()` 之前调用；mathkb 的
Settings 用 `lru_cache`，因此装配器会显式构造 Settings 并清理缓存。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = HARNESS_ROOT  # 副本根目录（src/ 与 .env 均在此）
SRC_DIR = PROJECT_ROOT / "src"
# harness 专用库：由 seed 脚本在副本内新建（9,921 块 / 335 人物），schema 与新代码一致。
# 原项目自带的 .index/mathkb.db（684 块、旧 schema）保持只读、不被 harness 修改。
INDEX_DB = PROJECT_ROOT / ".index" / "harness.db"
ORIGINAL_INDEX_DB = PROJECT_ROOT / ".index" / "mathkb.db"
DATA_FULL = PROJECT_ROOT / "data" / "full"
BIO_DIR = DATA_FULL / "biographies"
# 副本独立的缓存/索引目录：必须覆盖 .env 中写死的原项目绝对路径，
# 否则 BM25Store 会把缓存写回原项目目录（隔离失效）。
KEYWORD_DIR = PROJECT_ROOT / ".index" / "bm25-harness"
EMBEDDING_CACHE_DIR = PROJECT_ROOT / ".index" / "embedding-cache-harness"
VECTOR_PERSIST_DIR = PROJECT_ROOT / ".index" / "vectors-harness"

# 这些环境变量只在进程内生效；不修改 .env 文件。
_OFFLINE_OVERRIDES: dict[str, str] = {
    "MATHKB_LLM__PROVIDER": "stub",       # 默认离线；真实模型由 runner/panel 显式注入
    "MATHKB_EMBEDDING__PROVIDER": "hash",  # 离线可复现，不下载 bge 模型
    "MATHKB_VECTOR__PROVIDER": "memory",   # 不依赖 pgvector
    "MATHKB_RERANK__ENABLED": "false",
    "MATHKB_CACHE__ENABLED": "false",      # 避免 redis 依赖
    "MATHKB_CREATE_SCHEMA": "false",       # 不建表、不写原索引，只读现有库
    "MATHKB_DATA_DIR": str(DATA_FULL),
    "MATHKB_BIOGRAPHIES_DIR": str(BIO_DIR),
    "MATHKB_INDEX_DIR": str(PROJECT_ROOT / ".index"),
    "MATHKB_KEYWORD__PERSIST_DIR": str(KEYWORD_DIR),
    "MATHKB_EMBEDDING__CACHE_DIR": str(EMBEDDING_CACHE_DIR),
    "MATHKB_EMBEDDING__VECTOR_CACHE_DIR": str(EMBEDDING_CACHE_DIR),
    "MATHKB_VECTOR__PERSIST_DIR": str(VECTOR_PERSIST_DIR),
}

# 显式暴露给调用方，供文档与测试核对。
OFFLINE_ENV_KEYS = tuple(sorted(_OFFLINE_OVERRIDES))


def apply_offline_env(*, database_url: str | None = None) -> dict[str, str]:
    """把离线运行所需覆盖项写入当前进程环境变量，返回实际写入的键值对。

    不会写任何文件；`database_url` 默认指向副本内的预建 SQLite 索引（只读使用）。
    """
    values = dict(_OFFLINE_OVERRIDES)
    values["MATHKB_DATABASE_URL"] = database_url or f"sqlite:///{INDEX_DB.as_posix()}"
    for key, value in values.items():
        os.environ[key] = value
    return dict(values)


@dataclass
class NativeRuntime:
    """持有原项目装配好的上下文与问答服务。"""

    ctx: object
    chat: object
    index_stats: dict
    mode: str = "offline"

    @property
    def retriever(self):
        return self.ctx.retriever

    @property
    def graph(self):
        return self.ctx.graph

    @property
    def person_names(self) -> list[str]:
        graph = self.graph
        return list(graph.names) if graph is not None else []

    def close(self) -> None:
        closer = getattr(self.ctx, "close", None)
        if callable(closer):
            closer()


def create_runtime(
    *,
    database_url: str | None = None,
    llm_config: dict | None = None,
    apply_env: bool = True,
) -> NativeRuntime:
    """在副本内装配 mathkb 运行时。

    参数
    ----
    database_url: 覆盖数据库 URL；默认使用副本内 `.index/mathkb.db`。
    llm_config: 可选 {"provider","base_url","model","api_key"}。仅通过环境变量注入，
        其中 api_key 不落盘、不记录日志。为空时沿用离线 stub 默认。
    apply_env: 是否写入离线覆盖环境变量。测试注入自定义 Settings 时可设为 False。

    返回
    ----
    NativeRuntime，含 ctx / chat / index_stats。调用方负责在结束时 close()。
    """
    if apply_env:
        apply_offline_env(database_url=database_url)
    if llm_config:
        for key in ("provider", "base_url", "model", "api_key"):
            if llm_config.get(key):
                os.environ[f"MATHKB_LLM__{key.upper()}"] = str(llm_config[key]).strip()

    # 必须在 get_settings 之前清理缓存，确保读取到刚注入的进程环境变量。
    from mathkb.core import config as config_module

    get_settings_fn = getattr(config_module, "get_settings", None)
    cache_clear = getattr(get_settings_fn, "cache_clear", None)
    if callable(cache_clear):
        cache_clear()

    from mathkb.core.config import get_settings
    from mathkb.services.chat_service import ChatService
    from mathkb.services.container import AppContext

    settings = get_settings()
    ctx = AppContext.create(settings=settings, seed=False)  # 只读现有库，不 seed
    stats = ctx.build_index()
    return NativeRuntime(ctx=ctx, chat=ChatService(ctx), index_stats=dict(stats),
                         mode=(settings.llm.provider or "offline"))


__all__ = [
    "HARNESS_ROOT",
    "PROJECT_ROOT",
    "SRC_DIR",
    "OFFLINE_ENV_KEYS",
    "apply_offline_env",
    "NativeRuntime",
    "create_runtime",
]
