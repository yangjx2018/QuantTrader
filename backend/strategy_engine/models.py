"""strategy_engine 模块的 SQLAlchemy ORM 模型。

模型：
- Strategy：策略主档（当前生效代码 + 参数）
- StrategyVersion：策略版本快照

变更说明（vs 旧 `_db.py`）：
- 表名保持单数 `strategy` / `strategy_version`（与原设计一致）
- Strategy 删除冗余字段 entry_rules / exit_rules / risk_rules（DSL 全在 code_content）
- Strategy 新增 code_content 字段（当前生效代码）
- 统一使用 common.database.Base/TimestampMixin（不再重复定义 Base/engine/session）
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, Boolean, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from common.database import Base, TimestampMixin


class Strategy(Base, TimestampMixin):
    """策略主档。"""

    __tablename__ = "strategy"
    __table_args__ = (
        UniqueConstraint("code", name="uk_strategy_code"),
        Index("idx_strategy_type", "strategy_type"),
        Index("idx_strategy_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), comment="策略编码")
    name: Mapped[str] = mapped_column(String(128), comment="策略名称")
    strategy_type: Mapped[str] = mapped_column(String(32), comment="策略类型：trend/mean_reversion/arbitrage/sentiment")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="策略描述")
    status: Mapped[str] = mapped_column(String(16), default="draft", comment="状态：draft/active/archived")
    version: Mapped[str] = mapped_column(String(32), default="1.0.0", comment="当前版本号")
    code_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="当前生效的 Python 策略代码")
    parameters: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="默认参数")
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, comment="标签数组")
    author: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="创建者")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否默认策略")


class StrategyVersion(Base, TimestampMixin):
    """策略版本快照。每次保存策略代码变更时插入一条。"""

    __tablename__ = "strategy_version"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uk_strategy_version"),
        Index("ix_sv_strategy", "strategy_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(comment="关联 strategy.id")
    version: Mapped[str] = mapped_column(String(32), comment="版本号")
    change_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="变更日志")
    code_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="代码快照")
    parameters: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="参数快照")
    status: Mapped[str] = mapped_column(String(16), default="active", comment="状态：active/historical")
    backtest_result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="该版本的回测结果摘要")


__all__ = ["Strategy", "StrategyVersion"]
