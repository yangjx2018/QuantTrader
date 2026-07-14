"""strategy_engine 模块的数据访问层。

变更说明：
- 从 `._db` 改为 `.models` 导入 ORM 模型
- 新增 `list_options(status)` 方法（给 /api/strategy/options 用）
- 删除字段引用 entry_rules/exit_rules/risk_rules（已从 ORM 中删除）
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Strategy, StrategyVersion


class StrategyRepository:
    """策略主档 CRUD。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> Strategy:
        strategy = Strategy(**data)
        self.session.add(strategy)
        await self.session.flush()
        await self.session.refresh(strategy)
        return strategy

    async def get_by_id(self, strategy_id: int) -> Optional[Strategy]:
        result = await self.session.execute(
            select(Strategy).where(Strategy.id == strategy_id)
        )
        return result.scalar_one_or_none()

    async def get_by_code(self, code: str) -> Optional[Strategy]:
        result = await self.session.execute(
            select(Strategy).where(Strategy.code == code)
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        status: Optional[str] = None,
        strategy_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Strategy]:
        query = select(Strategy)
        if status:
            query = query.where(Strategy.status == status)
        if strategy_type:
            query = query.where(Strategy.strategy_type == strategy_type)
        query = query.order_by(Strategy.updated_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_options(
        self,
        status: Optional[str] = "active",
    ) -> list[Strategy]:
        """简化查询：仅返回指定 status 的策略，给 /options 接口用。

        Args:
            status: 默认 "active"；传 None 表示不过滤

        Returns:
            Strategy 列表（含全部字段，调用方只取 id/name/description/strategy_type）
        """
        query = select(Strategy)
        if status:
            query = query.where(Strategy.status == status)
        query = query.order_by(Strategy.id.asc())
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update(self, strategy_id: int, data: dict) -> Optional[Strategy]:
        await self.session.execute(
            update(Strategy).where(Strategy.id == strategy_id).values(**data)
        )
        await self.session.flush()
        return await self.get_by_id(strategy_id)

    async def delete(self, strategy_id: int) -> bool:
        result = await self.session.execute(
            delete(Strategy).where(Strategy.id == strategy_id)
        )
        return result.rowcount > 0

    async def count(self, status: Optional[str] = None) -> int:
        query = select(func.count(Strategy.id))
        if status:
            query = query.where(Strategy.status == status)
        result = await self.session.execute(query)
        return result.scalar() or 0


class StrategyVersionRepository:
    """策略版本快照 CRUD。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> StrategyVersion:
        version = StrategyVersion(**data)
        self.session.add(version)
        await self.session.flush()
        await self.session.refresh(version)
        return version

    async def get_by_id(self, version_id: int) -> Optional[StrategyVersion]:
        result = await self.session.execute(
            select(StrategyVersion).where(StrategyVersion.id == version_id)
        )
        return result.scalar_one_or_none()

    async def list_by_strategy(
        self,
        strategy_id: int,
        status: Optional[str] = None,
    ) -> list[StrategyVersion]:
        query = select(StrategyVersion).where(StrategyVersion.strategy_id == strategy_id)
        if status:
            query = query.where(StrategyVersion.status == status)
        query = query.order_by(StrategyVersion.created_at.desc())
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_latest(self, strategy_id: int) -> Optional[StrategyVersion]:
        result = await self.session.execute(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy_id)
            .order_by(StrategyVersion.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def delete_by_strategy(self, strategy_id: int) -> int:
        """级联删除某策略的全部版本。返回删除条数。"""
        result = await self.session.execute(
            delete(StrategyVersion).where(StrategyVersion.strategy_id == strategy_id)
        )
        return result.rowcount


__all__ = ["StrategyRepository", "StrategyVersionRepository"]
