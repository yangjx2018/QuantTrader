"""strategy_engine 模块的数据访问层。

变更说明：
- 从 `._db` 改为 `.models` 导入 ORM 模型
- 新增 `list_options(status)` 方法（给 /api/strategy/options 用）
- 删除字段引用 entry_rules/exit_rules/risk_rules（已从 ORM 中删除）
- Strategy.code_content 不落库主表：读写走 strategy_version，并用 builtin 兜底
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Strategy, StrategyVersion


def resolve_builtin_code(code: str, name: str = "") -> Optional[str]:
    """按策略编码/名称匹配内置策略源码（legacy DB 无版本记录时兜底）。"""
    from strategy_engine.builtin import (
        BOLLINGER_CODE,
        DOUBLE_MA_CODE,
        MACD_CODE,
        RSI_CODE,
    )

    key = f"{code or ''} {name or ''}".upper()
    rules: list[tuple[tuple[str, ...], str]] = [
        (("MA_CROSS", "DOUBLE_MA", "双均线"), DOUBLE_MA_CODE),
        (("RSI",), RSI_CODE),
        (("BOLL", "BOLLINGER"), BOLLINGER_CODE),
        (("MACD",), MACD_CODE),
    ]
    for needles, source in rules:
        if any(n.upper() in key for n in needles):
            return source
    return None


class StrategyRepository:
    """策略主档 CRUD。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def _hydrate_code(self, strategy: Optional[Strategy]) -> Optional[Strategy]:
        """从 strategy_version 或内置策略填充 code_content。"""
        if strategy is None:
            return None
        if strategy.code_content:
            return strategy

        ver = await StrategyVersionRepository(self.session).get_latest(strategy.id)
        if ver and ver.code_content:
            strategy.code_content = ver.code_content
            return strategy

        builtin = resolve_builtin_code(strategy.code, strategy.name or "")
        if builtin:
            strategy.code_content = builtin
        return strategy

    async def create(self, data: dict) -> Strategy:
        payload = dict(data)
        code_content = payload.pop("code_content", None)
        strategy = Strategy(**payload)
        self.session.add(strategy)
        await self.session.flush()

        if code_content:
            await StrategyVersionRepository(self.session).create(
                {
                    "strategy_id": strategy.id,
                    "version": strategy.version or "1.0.0",
                    "change_log": "initial",
                    "code_content": code_content,
                    "parameters": strategy.parameters,
                    "status": "active",
                }
            )
            strategy.code_content = code_content

        await self.session.refresh(strategy)
        if code_content:
            strategy.code_content = code_content
        return strategy

    async def get_by_id(self, strategy_id: int) -> Optional[Strategy]:
        result = await self.session.execute(
            select(Strategy).where(Strategy.id == strategy_id)
        )
        return await self._hydrate_code(result.scalar_one_or_none())

    async def get_by_code(self, code: str) -> Optional[Strategy]:
        result = await self.session.execute(
            select(Strategy).where(Strategy.code == code)
        )
        return await self._hydrate_code(result.scalar_one_or_none())

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
        # 列表接口不强制回填完整代码，避免 N+1
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
        import time

        payload = dict(data)
        code_content = payload.pop("code_content", None)

        if payload:
            await self.session.execute(
                update(Strategy).where(Strategy.id == strategy_id).values(**payload)
            )

        if code_content is not None:
            current = await self.session.execute(
                select(Strategy).where(Strategy.id == strategy_id)
            )
            strategy = current.scalar_one_or_none()
            if strategy is None:
                return None
            # (strategy_id, version) 唯一：未显式升版本时追加时间戳避免冲突
            base_ver = payload.get("version") or strategy.version or "1.0.0"
            version_label = (
                payload["version"]
                if "version" in payload
                else f"{base_ver}+{int(time.time())}"
            )
            await StrategyVersionRepository(self.session).create(
                {
                    "strategy_id": strategy_id,
                    "version": version_label,
                    "change_log": "code update",
                    "code_content": code_content,
                    "parameters": payload.get("parameters", strategy.parameters),
                    "status": "active",
                }
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


__all__ = [
    "StrategyRepository",
    "StrategyVersionRepository",
    "resolve_builtin_code",
]
