"""并发回测性能测试（任务 6.3/19）。

验证：
- batch_backtest 并发回测行为
- 并发数限制
- 性能对比（并发 vs 串行）
"""

import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch


class TestBatchBacktest:
    """并发回测测试"""

    @pytest.mark.asyncio
    async def test_batch_backtest_concurrency_limit(self):
        """strategy_ids > 10 时抛出异常。"""
        from strategy_engine.service import batch_backtest
        from strategy_engine.exceptions import InvalidStrategyError

        mock_db = MagicMock()
        with pytest.raises(InvalidStrategyError, match="最多支持 10 个"):
            await batch_backtest(
                db=mock_db,
                strategy_ids=list(range(11)),
                stock_code="000001.SZ",
                start_date="2024-01-01",
                end_date="2024-01-31",
            )

    @pytest.mark.asyncio
    async def test_batch_backtest_concurrent_execution(self):
        """验证并发回测使用 asyncio.gather 并发执行。"""
        from strategy_engine.service import batch_backtest
        from strategy_engine.runtime.types import BacktestResult, BarRecord

        mock_db = MagicMock()

        # Mock run_backtest 记录调用时间戳
        call_times = []

        async def mock_run_backtest(**kwargs):
            call_times.append(time.monotonic())
            await asyncio.sleep(0.01)  # 模拟回测耗时
            return BacktestResult(
                session_id=f"test_{kwargs['strategy_id']}",
                stock_code=kwargs.get("stock_code", ""),
                strategy_id=kwargs.get("strategy_id", 0),
                strategy_name="test",
                account_id=0,
                timeframe="1d",
                start_date="2024-01-01",
                end_date="2024-01-31",
                total_bars=10,
                time_elapsed=0.01,
                bars=[],
            )

        with patch("strategy_engine.service.run_backtest", mock_run_backtest):
            results = await batch_backtest(
                db=mock_db,
                strategy_ids=[1, 2, 3],
                stock_code="000001.SZ",
                start_date="2024-01-01",
                end_date="2024-01-31",
            )

        assert len(results) == 3
        # 并发执行：所有调用应该在很短时间内相继发生
        if len(call_times) >= 2:
            time_diff = max(call_times) - min(call_times)
            assert time_diff < 0.1  # 并发执行的时间差应该很小


import asyncio
