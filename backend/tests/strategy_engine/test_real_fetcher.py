"""测试 Real Fetcher 实现（任务 2.1-2.8）。

验证：
- RealKLineFetcher 对接 api_data
- RealAccountFetcher 对接 account_trading
- 异常处理与降级策略
- service.run_backtest 切换逻辑（USE_REAL_DATA 环境变量）
"""

import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from strategy_engine.service import run_backtest
from strategy_engine.runtime.real_data import RealKLineFetcher, RealAccountFetcher
from strategy_engine.runtime.engine import KBar, AccountConfig, DataUnavailableError
from strategy_engine.models import Strategy


@pytest.fixture
def mock_db():
    """Mock AsyncSession。"""
    return MagicMock(spec=AsyncSession)


@pytest.fixture
def mock_strategy():
    """Mock Strategy 对象。"""
    strategy = MagicMock(spec=Strategy)
    strategy.id = 1
    strategy.code = "TEST_STRATEGY"
    strategy.name = "测试策略"
    strategy.status = "active"
    strategy.code_content = """
def initialize(context):
    context.security = '000001.SZ'

def handle_data(context, data):
    pass
"""
    strategy.parameters = {}
    return strategy


# ============================================================
# 2.1-2.3: RealKLineFetcher 测试
# ============================================================

class TestRealKLineFetcher:
    """测试 RealKLineFetcher 对接 api_data。"""

    @pytest.mark.asyncio
    async def test_fetch_klines_success(self, mock_db):
        """成功从 api_data 获取 K 线数据。"""
        fetcher = RealKLineFetcher(mock_db)

        # Mock api_data.KLineService.get_kline_data
        mock_klines = [
            {
                "date": "2024-01-01",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 1000000,
                "amount": 10200000.0,
            },
            {
                "date": "2024-01-02",
                "open": 10.2,
                "high": 10.8,
                "low": 10.1,
                "close": 10.5,
                "volume": 1200000,
                "amount": 12600000.0,
            },
        ]

        with patch("api_data.service.KLineService") as mock_kline_service_class:
            mock_kline_service = AsyncMock()
            mock_kline_service.get_kline_data = AsyncMock(return_value=mock_klines)
            mock_kline_service_class.return_value = mock_kline_service

            with patch("api_data.repository.KLineRepository"):
                with patch("api_data.adapters.mock.MockAdapter"):
                    kbars = await fetcher.fetch_klines(
                        stock_code="000001.SZ",
                        start_date="2024-01-01",
                        end_date="2024-01-02",
                        timeframe="1d",
                    )

        assert len(kbars) == 2
        assert isinstance(kbars[0], KBar)
        assert kbars[0].time == "2024-01-01"
        assert kbars[0].close == 10.2
        assert kbars[1].time == "2024-01-02"
        assert kbars[1].close == 10.5

    @pytest.mark.asyncio
    async def test_fetch_klines_empty_data_fallback_to_mock(self, mock_db):
        """api_data 返回空数据时降级到 MockKLineFetcher。"""
        fetcher = RealKLineFetcher(mock_db)

        with patch("api_data.service.KLineService") as mock_kline_service_class:
            mock_kline_service = AsyncMock()
            mock_kline_service.get_kline_data = AsyncMock(return_value=[])
            mock_kline_service_class.return_value = mock_kline_service

            with patch("api_data.repository.KLineRepository"):
                with patch("api_data.adapters.mock.MockAdapter"):
                    kbars = await fetcher.fetch_klines(
                        stock_code="000001.SZ",
                        start_date="2024-01-01",
                        end_date="2024-01-02",
                        timeframe="1d",
                    )

        # 应该降级到 mock 数据（60 bars）
        assert len(kbars) == 60
        assert isinstance(kbars[0], KBar)

    @pytest.mark.asyncio
    async def test_fetch_klines_api_error_fallback_to_mock(self, mock_db):
        """api_data 抛出异常时降级到 MockKLineFetcher。"""
        fetcher = RealKLineFetcher(mock_db)

        with patch("api_data.service.KLineService") as mock_kline_service_class:
            mock_kline_service = AsyncMock()
            mock_kline_service.get_kline_data = AsyncMock(side_effect=Exception("API error"))
            mock_kline_service_class.return_value = mock_kline_service

            with patch("api_data.repository.KLineRepository"):
                with patch("api_data.adapters.mock.MockAdapter"):
                    kbars = await fetcher.fetch_klines(
                        stock_code="000001.SZ",
                        start_date="2024-01-01",
                        end_date="2024-01-02",
                        timeframe="1d",
                    )

        # 应该降级到 mock 数据
        assert len(kbars) == 60
        assert isinstance(kbars[0], KBar)


# ============================================================
# 2.4-2.6: RealAccountFetcher 测试
# ============================================================

class TestRealAccountFetcher:
    """测试 RealAccountFetcher 对接 account_trading。"""

    @pytest.mark.asyncio
    async def test_fetch_account_success(self, mock_db):
        """成功从 account_trading 获取账户配置。"""
        fetcher = RealAccountFetcher(mock_db)

        # Mock account_trading 返回
        mock_account = MagicMock()
        mock_account.account_id = 1
        mock_account.account_type = "virtual"
        mock_account.initial_capital = 1000000.0
        mock_account.commission_rate = 0.001
        mock_account.slippage = 0.0

        with patch("account_trading.repository.AccountTradingRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo.get_account = AsyncMock(return_value=mock_account)
            mock_repo_class.return_value = mock_repo

            account_config = await fetcher.fetch_account(account_id=1)

        assert isinstance(account_config, AccountConfig)
        assert account_config.account_id == 1
        assert account_config.initial_capital == 1000000.0
        assert account_config.commission_rate == 0.001

    @pytest.mark.asyncio
    async def test_fetch_account_not_found_returns_default(self, mock_db):
        """账户不存在时返回默认配置。"""
        fetcher = RealAccountFetcher(mock_db)

        with patch("account_trading.repository.AccountTradingRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo.get_account = AsyncMock(return_value=None)
            mock_repo_class.return_value = mock_repo

            account_config = await fetcher.fetch_account(account_id=999)

        # 应该返回默认配置
        assert isinstance(account_config, AccountConfig)
        assert account_config.account_id == 999
        assert account_config.initial_capital == 1000000.0  # 默认值
        assert account_config.commission_rate == 0.001  # 默认值

    @pytest.mark.asyncio
    async def test_fetch_account_api_error_returns_default(self, mock_db):
        """account_trading 抛出异常时返回默认配置。"""
        fetcher = RealAccountFetcher(mock_db)

        with patch("account_trading.repository.AccountTradingRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo.get_account = AsyncMock(side_effect=Exception("DB error"))
            mock_repo_class.return_value = mock_repo

            account_config = await fetcher.fetch_account(account_id=1)

        # 应该返回默认配置（降级策略）
        assert isinstance(account_config, AccountConfig)
        assert account_config.initial_capital == 1000000.0  # 默认值


# ============================================================
# 2.7: service.run_backtest 切换逻辑测试
# ============================================================

class TestRunBacktestSwitchingLogic:
    """测试 service.run_backtest 根据 USE_REAL_DATA 环境变量切换 fetchers。"""

    @pytest.mark.asyncio
    async def test_run_backtest_use_mock_data_by_default(self, mock_db, mock_strategy):
        """默认情况下（USE_REAL_DATA 未设置）使用 mock fetchers。"""
        # Mock StrategyRepository
        with patch("strategy_engine.service.StrategyRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo.get_by_id = AsyncMock(return_value=mock_strategy)
            mock_repo_class.return_value = mock_repo

            # 确保 USE_REAL_DATA 未设置
            os.environ.pop("USE_REAL_DATA", None)

            # Mock BacktestEngine.run
            with patch("strategy_engine.service.BacktestEngine") as mock_engine_class:
                mock_engine = AsyncMock()
                mock_engine.run = AsyncMock(return_value=MagicMock())
                mock_engine_class.return_value = mock_engine

                await run_backtest(
                    db=mock_db,
                    stock_code="000001.SZ",
                    strategy_id=1,
                    account_id=1,
                    timeframe="1d",
                    start_date="2024-01-01",
                    end_date="2024-01-31",
                )

                # 验证 BacktestEngine 被调用时使用了 MockKLineFetcher
                call_args = mock_engine_class.call_args
                kline_fetcher = call_args.kwargs["kline_fetcher"]
                account_fetcher = call_args.kwargs["account_fetcher"]

                # 应该是 Mock fetchers
                assert "MockKLineFetcher" in type(kline_fetcher).__name__
                assert "MockAccountFetcher" in type(account_fetcher).__name__

    @pytest.mark.asyncio
    async def test_run_backtest_use_real_data_when_enabled(self, mock_db, mock_strategy):
        """USE_REAL_DATA=true 时使用 real fetchers。"""
        # Mock StrategyRepository
        with patch("strategy_engine.service.StrategyRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo.get_by_id = AsyncMock(return_value=mock_strategy)
            mock_repo_class.return_value = mock_repo

            # 设置 USE_REAL_DATA=true
            os.environ["USE_REAL_DATA"] = "true"

            try:
                # Mock BacktestEngine.run
                with patch("strategy_engine.service.BacktestEngine") as mock_engine_class:
                    mock_engine = AsyncMock()
                    mock_engine.run = AsyncMock(return_value=MagicMock())
                    mock_engine_class.return_value = mock_engine

                    await run_backtest(
                        db=mock_db,
                        stock_code="000001.SZ",
                        strategy_id=1,
                        account_id=1,
                        timeframe="1d",
                        start_date="2024-01-01",
                        end_date="2024-01-31",
                    )

                    # 验证 BacktestEngine 被调用时使用了 RealKLineFetcher
                    call_args = mock_engine_class.call_args
                    kline_fetcher = call_args.kwargs["kline_fetcher"]
                    account_fetcher = call_args.kwargs["account_fetcher"]

                    # 应该是 Real fetchers
                    assert "RealKLineFetcher" in type(kline_fetcher).__name__
                    assert "RealAccountFetcher" in type(account_fetcher).__name__
            finally:
                # 清理环境变量
                os.environ.pop("USE_REAL_DATA", None)

    @pytest.mark.asyncio
    async def test_run_backtest_use_real_data_with_1(self, mock_db, mock_strategy):
        """USE_REAL_DATA=1 时也使用 real fetchers。"""
        # Mock StrategyRepository
        with patch("strategy_engine.service.StrategyRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo.get_by_id = AsyncMock(return_value=mock_strategy)
            mock_repo_class.return_value = mock_repo

            # 设置 USE_REAL_DATA=1
            os.environ["USE_REAL_DATA"] = "1"

            try:
                # Mock BacktestEngine.run
                with patch("strategy_engine.service.BacktestEngine") as mock_engine_class:
                    mock_engine = AsyncMock()
                    mock_engine.run = AsyncMock(return_value=MagicMock())
                    mock_engine_class.return_value = mock_engine

                    await run_backtest(
                        db=mock_db,
                        stock_code="000001.SZ",
                        strategy_id=1,
                        account_id=1,
                        timeframe="1d",
                        start_date="2024-01-01",
                        end_date="2024-01-31",
                    )

                    # 验证使用了 Real fetchers
                    call_args = mock_engine_class.call_args
                    kline_fetcher = call_args.kwargs["kline_fetcher"]
                    assert "RealKLineFetcher" in type(kline_fetcher).__name__
            finally:
                os.environ.pop("USE_REAL_DATA", None)

    @pytest.mark.asyncio
    async def test_run_backtest_use_real_data_with_yes(self, mock_db, mock_strategy):
        """USE_REAL_DATA=yes 时也使用 real fetchers。"""
        # Mock StrategyRepository
        with patch("strategy_engine.service.StrategyRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo.get_by_id = AsyncMock(return_value=mock_strategy)
            mock_repo_class.return_value = mock_repo

            # 设置 USE_REAL_DATA=yes
            os.environ["USE_REAL_DATA"] = "yes"

            try:
                # Mock BacktestEngine.run
                with patch("strategy_engine.service.BacktestEngine") as mock_engine_class:
                    mock_engine = AsyncMock()
                    mock_engine.run = AsyncMock(return_value=MagicMock())
                    mock_engine_class.return_value = mock_engine

                    await run_backtest(
                        db=mock_db,
                        stock_code="000001.SZ",
                        strategy_id=1,
                        account_id=1,
                        timeframe="1d",
                        start_date="2024-01-01",
                        end_date="2024-01-31",
                    )

                    # 验证使用了 Real fetchers
                    call_args = mock_engine_class.call_args
                    kline_fetcher = call_args.kwargs["kline_fetcher"]
                    assert "RealKLineFetcher" in type(kline_fetcher).__name__
            finally:
                os.environ.pop("USE_REAL_DATA", None)

    @pytest.mark.asyncio
    async def test_run_backtest_use_mock_data_when_false(self, mock_db, mock_strategy):
        """USE_REAL_DATA=false 时使用 mock fetchers。"""
        # Mock StrategyRepository
        with patch("strategy_engine.service.StrategyRepository") as mock_repo_class:
            mock_repo = AsyncMock()
            mock_repo.get_by_id = AsyncMock(return_value=mock_strategy)
            mock_repo_class.return_value = mock_repo

            # 设置 USE_REAL_DATA=false
            os.environ["USE_REAL_DATA"] = "false"

            try:
                # Mock BacktestEngine.run
                with patch("strategy_engine.service.BacktestEngine") as mock_engine_class:
                    mock_engine = AsyncMock()
                    mock_engine.run = AsyncMock(return_value=MagicMock())
                    mock_engine_class.return_value = mock_engine

                    await run_backtest(
                        db=mock_db,
                        stock_code="000001.SZ",
                        strategy_id=1,
                        account_id=1,
                        timeframe="1d",
                        start_date="2024-01-01",
                        end_date="2024-01-31",
                    )

                    # 验证使用了 Mock fetchers
                    call_args = mock_engine_class.call_args
                    kline_fetcher = call_args.kwargs["kline_fetcher"]
                    assert "MockKLineFetcher" in type(kline_fetcher).__name__
            finally:
                os.environ.pop("USE_REAL_DATA", None)
