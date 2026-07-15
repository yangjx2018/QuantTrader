"""策略分享服务单元测试（任务 6.4）。

验证：
- SharingService.export_strategies 导出逻辑
- SharingService.import_strategies 导入逻辑
- 重复策略跳过
- 策略不存在场景
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from strategy_engine.runtime.sharing import (
    SharingService,
    StrategyExportItem,
    ImportResult,
)
from strategy_engine.exceptions import StrategyNotFound


class TestSharingServiceExport:
    """策略导出测试"""

    @pytest.mark.asyncio
    async def test_export_strategy_not_found(self):
        """导出不存在的策略返回 404。"""
        svc = SharingService()
        mock_db = MagicMock()

        with patch("strategy_engine.runtime.sharing.StrategyRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo.get_by_id = AsyncMock(return_value=None)
            mock_repo_class.return_value = mock_repo

            with pytest.raises(StrategyNotFound, match="不存在"):
                await svc.export_strategies(mock_db, [99999])

    @pytest.mark.asyncio
    async def test_export_strategy_success(self):
        """导出策略成功。"""
        svc = SharingService()
        mock_db = MagicMock()

        mock_strategy = MagicMock()
        mock_strategy.code = "TEST_EXPORT"
        mock_strategy.name = "测试导出"
        mock_strategy.strategy_type = "trend"
        mock_strategy.description = "测试描述"
        mock_strategy.code_content = "def initialize(ctx): pass"
        mock_strategy.parameters = {"period": 20}

        with patch("strategy_engine.runtime.sharing.StrategyRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo.get_by_id = AsyncMock(return_value=mock_strategy)
            mock_repo_class.return_value = mock_repo

            with patch("strategy_engine.runtime.sharing.StrategyVersionRepository"):
                items = await svc.export_strategies(mock_db, [1])

        assert len(items) == 1
        assert items[0].code == "TEST_EXPORT"
        assert items[0].name == "测试导出"
        assert items[0].code_content == "def initialize(ctx): pass"

    @pytest.mark.asyncio
    async def test_export_with_versions(self):
        """导出包含版本历史。"""
        svc = SharingService()
        mock_db = MagicMock()

        mock_strategy = MagicMock()
        mock_strategy.code = "TEST_VERSION_EXPORT"
        mock_strategy.name = "版本导出"
        mock_strategy.strategy_type = "trend"
        mock_strategy.description = None
        mock_strategy.code_content = "code v2"
        mock_strategy.parameters = None

        mock_version = MagicMock()
        mock_version.version = "1.0.0"
        mock_version.code_content = "code v1"
        mock_version.parameters = None
        mock_version.change_log = "初始版本"
        mock_version.created_at = None

        with patch("strategy_engine.runtime.sharing.StrategyRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo.get_by_id = AsyncMock(return_value=mock_strategy)
            mock_repo_class.return_value = mock_repo

            with patch("strategy_engine.runtime.sharing.StrategyVersionRepository") as mock_ver_class:
                mock_ver = AsyncMock()
                mock_ver.list_by_strategy = AsyncMock(return_value=[mock_version])
                mock_ver_class.return_value = mock_ver

                items = await svc.export_strategies(
                    mock_db, [1], include_versions=True
                )

        assert len(items) == 1
        assert len(items[0].versions) == 1
        assert items[0].versions[0]["version"] == "1.0.0"


class TestSharingServiceImport:
    """策略导入测试"""

    @pytest.mark.asyncio
    async def test_import_strategy_success(self):
        """导入策略成功。"""
        svc = SharingService()
        mock_db = MagicMock()

        mock_strategy = MagicMock()
        mock_strategy.id = 100

        with patch("strategy_engine.runtime.sharing.StrategyRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo.get_by_code = AsyncMock(return_value=None)  # 不存在
            mock_repo.create = AsyncMock(return_value=mock_strategy)
            mock_repo_class.return_value = mock_repo

            with patch("strategy_engine.runtime.sharing.StrategyVersionRepository"):
                result = await svc.import_strategies(mock_db, [
                    {
                        "code": "TEST_IMPORT",
                        "name": "导入策略",
                        "strategy_type": "trend",
                        "code_content": "def initialize(ctx): pass",
                        "parameters": {"period": 20},
                    }
                ])

        assert result.imported == 1
        assert result.skipped == 0
        assert len(result.skipped_codes) == 0

    @pytest.mark.asyncio
    async def test_import_duplicate_skipped(self):
        """导入重复策略跳过。"""
        svc = SharingService()
        mock_db = MagicMock()

        mock_existing = MagicMock()
        mock_new = MagicMock()
        mock_new.id = 101

        with patch("strategy_engine.runtime.sharing.StrategyRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            # 第一个存在（跳过），第二个不存在（导入）
            mock_repo.get_by_code = AsyncMock(side_effect=[mock_existing, None])
            mock_repo.create = AsyncMock(return_value=mock_new)
            mock_repo_class.return_value = mock_repo

            with patch("strategy_engine.runtime.sharing.StrategyVersionRepository"):
                result = await svc.import_strategies(mock_db, [
                    {"code": "DUP_CODE", "name": "重复", "strategy_type": "trend",
                     "code_content": "pass"},
                    {"code": "NEW_CODE", "name": "新策略", "strategy_type": "trend",
                     "code_content": "pass"},
                ])

        assert result.imported == 1
        assert result.skipped == 1
        assert "DUP_CODE" in result.skipped_codes

    def test_import_result_dataclass(self):
        """ImportResult 数据类创建正常。"""
        result = ImportResult(
            imported=2,
            skipped=1,
            skipped_codes=["DUP_001"],
        )
        assert result.imported == 2
        assert result.skipped == 1
        assert result.skipped_codes == ["DUP_001"]

    def test_export_item_dataclass(self):
        """StrategyExportItem 数据类创建正常。"""
        item = StrategyExportItem(
            code="TEST",
            name="测试",
            strategy_type="trend",
            code_content="pass",
        )
        assert item.code == "TEST"
        assert item.versions == []
