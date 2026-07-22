"""add strategy.code_content column + backfill from strategy_version / builtins

Revision ID: a1b2c3d4e5f6
Revises: 4ad58868baa7
Create Date: 2026-07-18 18:30:00.000000

说明：
- 线上 Aliyun RDS 若处于 LOCK_WRITE，本迁移的 ALTER 会失败（Error 1290）。
  应用层已兼容：Strategy.code_content 不映射为列，从 strategy_version / builtin 回填。
- 解锁后执行本迁移，可将代码冗余写回主表。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "4ad58868baa7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns(table)}
    return column in cols


def upgrade() -> None:
    if not _column_exists("strategy", "code_content"):
        op.add_column(
            "strategy",
            sa.Column("code_content", sa.Text(), nullable=True, comment="当前生效的 Python 策略代码"),
        )

    # 从最新 strategy_version 回填
    op.execute(
        sa.text(
            """
            UPDATE strategy s
            LEFT JOIN (
                SELECT sv.strategy_id, sv.code_content
                FROM strategy_version sv
                INNER JOIN (
                    SELECT strategy_id, MAX(id) AS max_id
                    FROM strategy_version
                    GROUP BY strategy_id
                ) t ON sv.id = t.max_id
            ) latest ON s.id = latest.strategy_id
            SET s.code_content = latest.code_content
            WHERE (s.code_content IS NULL OR s.code_content = '')
              AND latest.code_content IS NOT NULL
              AND latest.code_content <> ''
            """
        )
    )


def downgrade() -> None:
    if _column_exists("strategy", "code_content"):
        op.drop_column("strategy", "code_content")
