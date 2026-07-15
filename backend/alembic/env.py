"""Alembic env.py — async + 从 common.config 取 DATABASE_URL。

特性：
- 使用 SQLAlchemy 2.0 async engine（与项目运行时一致）
- 从 common.config.Settings 读取 DATABASE_URL（不写在 alembic.ini）
- 自动 import 全部模块的 models 让 autogenerate 发现
- sys.path 加入 backend/ 根目录

参考资料：
- https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic
"""

from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# === 1. 把 backend/ 加进 sys.path，让 common.* / 各模块可 import ===
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# === 2. 加载 .env 配置 + 项目 Base ===
from common.config import get_settings  # noqa: E402
from common.database import Base  # noqa: E402

# 必须显式 import 各模块的 models，让 Base.metadata 知道全部表
# 这样 autogenerate 才能发现新增表
import account_trading.models  # noqa: F401,E402
import api_data.models  # noqa: F401,E402
import history_replay.models  # noqa: F401,E402
import review_analysis.models  # noqa: F401,E402
import strategy_engine.models  # noqa: F401,E402
import strategy_execution.models  # noqa: F401,E402

# === 3. Alembic 配置 ===
config = context.config

# 注入数据库 URL（从 .env 而不是 alembic.ini）
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# 日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 统一 metadata（用于 autogenerate 对比）
target_metadata = Base.metadata


# === 4. Offline / Online 模式 ===

def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL，不连数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """执行迁移核心逻辑。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """异步在线模式：用 async engine 创建连接。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式入口：交给 asyncio。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
