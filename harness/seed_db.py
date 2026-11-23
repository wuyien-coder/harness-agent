"""在副本内重建 harness 专用 SQLite 知识库（不触碰原项目 .index/mathkb.db）。

用法（从副本根目录运行）：
  PYTHONPATH=src:. D:/code/python.exe -m harness.seed_db

数据来自副本的 data/full/biographies（326 篇传记）+ data/full/kg（人物关系）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harness.bootstrap import (  # noqa: E402
    DATA_FULL,
    BIO_DIR,
    INDEX_DB,
    apply_offline_env,
)


def main() -> int:
    db_url = f"sqlite:///{INDEX_DB.as_posix()}"
    env = apply_offline_env(database_url=db_url)
    # 覆盖为「建表 + seed」模式（apply_offline_env 默认是只读）
    import os
    os.environ["MATHKB_CREATE_SCHEMA"] = "true"

    from mathkb.services.container import AppContext

    print(f"重建库: {INDEX_DB}")
    print(f"数据源: {BIO_DIR}")
    ctx = AppContext.create(seed=True)  # 自动建表 + seed（幂等：已有 Document 时跳过）
    stats = ctx.build_index()
    print("索引就绪:", {
        "chunks": stats.get("chunks"),
        "persons": stats.get("persons"),
        "relations": stats.get("relations"),
        "embedder": stats.get("embedder"),
    })
    ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
