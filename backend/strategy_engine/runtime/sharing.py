"""策略分享服务：导出/导入策略为 JSON 格式。

设计要点：
- 导出：支持批量导出，可选包含版本历史
- 导入：跳过重复策略（按 code 判断），支持导入版本历史
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from strategy_engine.exceptions import StrategyNotFound
from strategy_engine.repository import StrategyRepository, StrategyVersionRepository

logger = logging.getLogger(__name__)


@dataclass
class StrategyExportItem:
    """单个策略的导出数据。"""
    code: str
    name: str
    strategy_type: str
    description: Optional[str] = None
    code_content: str = ""
    parameters: Optional[dict] = None
    versions: list[dict] = field(default_factory=list)


@dataclass
class ImportResult:
    """导入结果。"""
    imported: int
    skipped: int
    skipped_codes: list[str] = field(default_factory=list)


class SharingService:
    """策略分享服务。

    用法：
        svc = SharingService()
        exported = await svc.export_strategies(db, strategy_ids, include_versions)
        result = await svc.import_strategies(db, strategies_data)
    """

    async def export_strategies(
        self,
        db: AsyncSession,
        strategy_ids: list[int],
        include_versions: bool = False,
    ) -> list[StrategyExportItem]:
        """导出策略为 JSON 格式。

        Args:
            db: 数据库会话
            strategy_ids: 策略 ID 列表
            include_versions: 是否包含版本历史

        Returns:
            list[StrategyExportItem]: 导出数据列表

        Raises:
            StrategyNotFound: 策略不存在
        """
        repo = StrategyRepository(db)
        version_repo = StrategyVersionRepository(db)
        results: list[StrategyExportItem] = []

        for sid in strategy_ids:
            strategy = await repo.get_by_id(sid)
            if not strategy:
                raise StrategyNotFound(f"策略 {sid} 不存在")

            item = StrategyExportItem(
                code=strategy.code,
                name=strategy.name,
                strategy_type=strategy.strategy_type,
                description=strategy.description,
                code_content=strategy.code_content or "",
                parameters=strategy.parameters,
            )

            if include_versions:
                versions = await version_repo.list_by_strategy(sid)
                item.versions = [
                    {
                        "version": v.version,
                        "code_content": v.code_content or "",
                        "parameters": v.parameters,
                        "change_log": v.change_log,
                        "created_at": v.created_at.isoformat() if v.created_at else None,
                    }
                    for v in versions
                ]

            results.append(item)

        logger.info("Exported %d strategies", len(results))
        return results

    async def import_strategies(
        self,
        db: AsyncSession,
        strategies_data: list[dict],
    ) -> ImportResult:
        """导入 JSON 格式的策略。

        Args:
            db: 数据库会话
            strategies_data: 策略数据列表（dict 格式）

        Returns:
            ImportResult: 导入结果（imported/skipped/skipped_codes）
        """
        repo = StrategyRepository(db)
        version_repo = StrategyVersionRepository(db)
        imported = 0
        skipped = 0
        skipped_codes: list[str] = []

        for data in strategies_data:
            code = data.get("code", "")
            existing = await repo.get_by_code(code)
            if existing:
                skipped += 1
                skipped_codes.append(code)
                logger.info("Skipping duplicate strategy: code=%s", code)
                continue

            # 创建策略
            strategy_data = {
                "code": code,
                "name": data.get("name", ""),
                "strategy_type": data.get("strategy_type", "trend"),
                "description": data.get("description"),
                "status": "draft",
                "version": data.get("version", "1.0.0"),
                "code_content": data.get("code_content", ""),
                "parameters": data.get("parameters"),
            }
            strategy = await repo.create(strategy_data)
            imported += 1

            # 导入版本历史
            versions = data.get("versions", [])
            for v in versions:
                version_data = {
                    "strategy_id": strategy.id,
                    "version": v.get("version", "1.0.0"),
                    "code_content": v.get("code_content", ""),
                    "parameters": v.get("parameters"),
                    "change_log": v.get("change_log"),
                    "status": "historical",
                }
                await version_repo.create(version_data)

            logger.info("Imported strategy: code=%s, id=%d", code, strategy.id)

        return ImportResult(
            imported=imported,
            skipped=skipped,
            skipped_codes=skipped_codes,
        )


__all__ = ["SharingService", "StrategyExportItem", "ImportResult"]
