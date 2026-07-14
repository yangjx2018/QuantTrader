"""版本对比服务单元测试（任务 5.3）。

验证：
- CompareService.compare 指标计算
- 版本不存在场景
- 版本数错误场景
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from strategy_engine.runtime.compare import CompareService, CompareDiff, CompareResult
from strategy_engine.exceptions import StrategyNotFound, InvalidStrategyError


class TestCompareService:
    """CompareService 单元测试"""

    def test_compare_diff_dataclass(self):
        """CompareDiff 数据类创建正常。"""
        diff = CompareDiff(
            total_return_pct=5.0,
            sharpe_ratio=0.5,
            max_drawdown_pct=-2.0,
            win_rate_pct=10.0,
            profit_loss_ratio=0.3,
        )
        assert diff.total_return_pct == 5.0
        assert diff.sharpe_ratio == 0.5

    @pytest.mark.asyncio
    async def test_compare_invalid_version_count(self):
        """版本数 != 2 时抛出异常。"""
        svc = CompareService()
        mock_db = MagicMock()

        with pytest.raises(InvalidStrategyError, match="2 个版本"):
            await svc.compare(
                db=mock_db,
                version_ids=[1],  # 只有 1 个
                stock_code="000001.SZ",
                start_date="2024-01-01",
                end_date="2024-01-31",
            )

    @pytest.mark.asyncio
    async def test_compare_version_not_found(self):
        """版本不存在时抛出 StrategyNotFound。"""
        svc = CompareService()
        mock_db = MagicMock()

        with patch(
            "strategy_engine.runtime.compare.StrategyVersionRepository"
        ) as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo.get_by_id = AsyncMock(return_value=None)
            mock_repo_class.return_value = mock_repo

            with pytest.raises(StrategyNotFound, match="不存在"):
                await svc.compare(
                    db=mock_db,
                    version_ids=[99999, 2],
                    stock_code="000001.SZ",
                    start_date="2024-01-01",
                    end_date="2024-01-31",
                )

    def test_calc_metrics_empty_result(self):
        """空回测结果返回默认指标。"""
        svc = CompareService()
        from strategy_engine.runtime.types import BacktestResult
        empty = BacktestResult(
            session_id="test",
            stock_code="000001.SZ",
            strategy_id=1,
            strategy_name="test",
            account_id=0,
            timeframe="1d",
            start_date="2024-01-01",
            end_date="2024-01-31",
            total_bars=0,
            time_elapsed=0.0,
            bars=[],
        )
        metrics = svc._calc_metrics(empty)
        assert metrics["total_return_pct"] == 0.0
        assert metrics["sharpe_ratio"] == 0.0
        assert metrics["max_drawdown_pct"] == 0.0
        assert metrics["win_rate_pct"] == 0.0

    def test_calc_metrics_with_data(self):
        """有数据的回测结果计算指标。"""
        svc = CompareService()
        from strategy_engine.runtime.types import (
            BacktestResult, BarRecord, OrderRecord,
        )

        bars = [
            BarRecord(
                time="2024-01-01", open=10.0, high=10.5, low=9.8, close=10.2,
                volume=1000000, amount=10200000.0, total_assets=200000.0,
                cash=200000.0,
            ),
            BarRecord(
                time="2024-01-02", open=10.2, high=10.8, low=10.1, close=10.5,
                volume=1200000, amount=12600000.0, total_assets=201000.0,
                cash=200000.0,
                orders=[
                    OrderRecord(
                        time="2024-01-02", side="buy", price=10.2, quantity=100,
                        amount=1020.0, commission=5.0, pnl=100.0, signal="buy",
                    )
                ],
            ),
        ]

        result = BacktestResult(
            session_id="test",
            stock_code="000001.SZ",
            strategy_id=1,
            strategy_name="test",
            account_id=0,
            timeframe="1d",
            start_date="2024-01-01",
            end_date="2024-01-31",
            total_bars=2,
            time_elapsed=0.1,
            bars=bars,
        )

        metrics = svc._calc_metrics(result)
        assert "total_bars" not in metrics  # 不含原始 bar 数
        assert isinstance(metrics["total_return_pct"], float)
        assert isinstance(metrics["sharpe_ratio"], float)
        assert isinstance(metrics["max_drawdown_pct"], float)
        assert isinstance(metrics["win_rate_pct"], float)
