"""strategy_engine.runtime.engine 回测引擎集成测试。

测试策略：
- 用 mock 的 KLineFetcher / BenchmarkFetcher / AccountFetcher
- 用一个简化的双均线策略，断言：
  - BacktestResult 字段完整
  - 买卖订单触发正确
  - BarRecord.bars 长度等于 K 线数
  - 撮合后 total_assets = cash + sum(positions.market_value)
"""

import pytest
import pytest_asyncio

from strategy_engine.runtime.engine import (
    AccountConfig,
    BacktestEngine,
    KBar,
)
from strategy_engine.runtime.types import BacktestResult


# ============================================================
# Mock 依赖
# ============================================================

class MockKLineFetcher:
    """构造 30 天的 K 线，价格围绕 10~12 震荡。"""

    def __init__(self, kbars: list[KBar] | None = None):
        self.kbars = kbars or self._default_kbars()

    @staticmethod
    def _default_kbars() -> list[KBar]:
        # 30 个交易日，价格交替上下以触发均线金叉/死叉
        prices = [
            10.0, 10.2, 10.5, 10.8, 11.0, 10.8, 10.5, 10.3, 10.0, 9.8,
            9.5, 9.8, 10.0, 10.3, 10.5, 10.8, 11.0, 11.2, 11.5, 11.8,
            12.0, 11.8, 11.5, 11.2, 11.0, 10.8, 10.5, 10.3, 10.0, 9.8,
        ]
        return [
            KBar(
                time=f"2024-01-{i+1:02d}",
                open=p, high=p + 0.1, low=p - 0.1, close=p,
                volume=1000000, amount=round(p * 1000000, 2),
            )
            for i, p in enumerate(prices)
        ]

    async def fetch_klines(self, stock_code, start_date, end_date, timeframe, fq="qfq"):
        return self.kbars


class MockBenchmarkFetcher:
    def __init__(self, prices: dict[str, float] | None = None):
        self.prices = prices or {}

    async def fetch_benchmark(self, index_code, start_date, end_date, timeframe):
        return self.prices


class MockAccountFetcher:
    def __init__(self, account: AccountConfig | None = None):
        self.account = account or AccountConfig(
            account_id=1, initial_capital=1_000_000.0, commission_rate=0.001
        )

    async def fetch_account(self, account_id):
        return self.account


class MockAccountFetcherFail:
    """模拟 account_trading 调用失败。"""
    async def fetch_account(self, account_id):
        raise RuntimeError("account service unavailable")


# ============================================================
# Mock 策略对象（duck typed，不需要 ORM）
# ============================================================

class MockStrategy:
    def __init__(self, code_content: str, parameters: dict | None = None):
        self.id = 1
        self.code = "TEST"
        self.name = "测试策略"
        self.strategy_type = "trend"
        self.status = "active"
        self.version = "1.0.0"
        self.code_content = code_content
        self.parameters = parameters or {}
        self.description = "测试"


# ============================================================
# 简单策略代码（用于测试引擎主循环）
# ============================================================

# 策略 A：固定在第 5 个 bar 买入 1000 股，第 20 个 bar 全部卖出
BUY_SELL_CODE = """
g.buy_done = False
g.sell_done = False

def initialize(context):
    pass

def handle_data(context, data):
    security = context.universe[0]
    price = data[security]['close']

    if not g.buy_done and context.current_dt in ('2024-01-05', '2024-01-06'):
        order_value(security, 11000)  # 约 1000 股
        g.buy_done = True
        log.info('买入信号')

    if not g.sell_done and context.current_dt == '2024-01-20':
        order_target(security, 0)
        g.sell_done = True
        log.info('卖出信号')
"""

# 策略 B：什么都不做
IDLE_CODE = """
def initialize(context):
    pass

def handle_data(context, data):
    pass
"""

# 策略 C：handle_data 抛异常（验证不中断）
EXCEPTION_CODE = """
def initialize(context):
    pass

def handle_data(context, data):
    if context.current_dt == '2024-01-10':
        raise ValueError('user code exception')
"""


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def engine():
    return BacktestEngine(
        kline_fetcher=MockKLineFetcher(),
        benchmark_fetcher=MockBenchmarkFetcher(prices={f"2024-01-{i:02d}": 3500.0 + i for i in range(1, 31)}),
        account_fetcher=MockAccountFetcher(),
    )


@pytest.fixture
def kbars():
    return MockKLineFetcher._default_kbars()


# ============================================================
# 测试用例
# ============================================================

class TestBacktestEngine:
    @pytest.mark.asyncio
    async def test_basic_run_returns_full_result(self, engine):
        """完整回测：30 个 bar，返回 BacktestResult 字段齐全。"""
        strategy = MockStrategy(BUY_SELL_CODE)
        result = await engine.run(
            strategy=strategy,
            stock_code="000001.SZ",
            account_id=1,
            timeframe="1d",
            start_date="2024-01-01",
            end_date="2024-01-30",
        )

        assert isinstance(result, BacktestResult)
        assert result.total_bars == 30
        assert result.stock_code == "000001.SZ"
        assert result.strategy_id == 1
        assert result.account_id == 1
        assert result.timeframe == "1d"
        assert result.time_elapsed >= 0  # 30 bar 可能 <1ms，不强制 > 0
        # 字段存在性
        assert all(b.time for b in result.bars)
        assert all(b.open > 0 for b in result.bars)
        assert all(b.benchmark_close > 0 for b in result.bars)

    @pytest.mark.asyncio
    async def test_idle_strategy_no_orders(self, engine):
        """空策略不产生任何订单。"""
        strategy = MockStrategy(IDLE_CODE)
        result = await engine.run(
            strategy=strategy,
            stock_code="000001.SZ",
            account_id=1,
            timeframe="1d",
            start_date="2024-01-01",
            end_date="2024-01-30",
        )
        all_orders = [o for b in result.bars for o in b.orders]
        assert len(all_orders) == 0
        # 总资产应等于初始资金（无手续费）
        assert result.bars[-1].total_assets == pytest.approx(1_000_000.0, rel=0.01)

    @pytest.mark.asyncio
    async def test_buy_then_sell_orders_triggered(self, engine):
        """买入/卖出订单按预期触发。"""
        strategy = MockStrategy(BUY_SELL_CODE)
        result = await engine.run(
            strategy=strategy,
            stock_code="000001.SZ",
            account_id=1,
            timeframe="1d",
            start_date="2024-01-01",
            end_date="2024-01-30",
        )

        buy_orders = [o for b in result.bars for o in b.orders if o.side == "buy"]
        sell_orders = [o for b in result.bars for o in b.orders if o.side == "sell"]
        assert len(buy_orders) == 1, f"期望 1 笔买入，实际 {len(buy_orders)}"
        assert len(sell_orders) == 1, f"期望 1 笔卖出，实际 {len(sell_orders)}"

        # 买入股数应是 100 整数倍
        assert buy_orders[0].quantity % 100 == 0
        # 卖出应有 pnl
        assert sell_orders[0].pnl != 0

    @pytest.mark.asyncio
    async def test_user_code_exception_does_not_break(self, engine):
        """用户代码抛异常时被捕获，记录到日志，不中断后续 bar。"""
        strategy = MockStrategy(EXCEPTION_CODE)
        result = await engine.run(
            strategy=strategy,
            stock_code="000001.SZ",
            account_id=1,
            timeframe="1d",
            start_date="2024-01-01",
            end_date="2024-01-30",
        )
        # 30 个 bar 全部产出（异常在第 10 个 bar，但不中断）
        assert result.total_bars == 30
        # 第 10 个 bar 的日志含 [ERROR]
        bar_10 = result.bars[9]
        assert any("[ERROR]" in entry and "ValueError" in entry for entry in bar_10.log_entries)

    @pytest.mark.asyncio
    async def test_strategy_not_active_raises(self, engine):
        """status != 'active' 时抛 StrategyNotActive。"""
        strategy = MockStrategy(IDLE_CODE)
        strategy.status = "draft"
        from strategy_engine.runtime.engine import StrategyNotActive
        with pytest.raises(StrategyNotActive):
            await engine.run(
                strategy=strategy,
                stock_code="000001.SZ",
                account_id=1,
                timeframe="1d",
                start_date="2024-01-01",
                end_date="2024-01-30",
            )

    @pytest.mark.asyncio
    async def test_empty_code_content_raises(self, engine):
        """策略代码为空时抛 InvalidStrategyError。"""
        strategy = MockStrategy(IDLE_CODE)
        strategy.code_content = ""
        from strategy_engine.runtime.loader import InvalidStrategyError
        with pytest.raises(InvalidStrategyError):
            await engine.run(
                strategy=strategy,
                stock_code="000001.SZ",
                account_id=1,
                timeframe="1d",
                start_date="2024-01-01",
                end_date="2024-01-30",
            )

    @pytest.mark.asyncio
    async def test_unsupported_timeframe_raises(self, engine):
        strategy = MockStrategy(IDLE_CODE)
        from strategy_engine.runtime.engine import BacktestError
        with pytest.raises(BacktestError, match="timeframe"):
            await engine.run(
                strategy=strategy,
                stock_code="000001.SZ",
                account_id=1,
                timeframe="1m",
                start_date="2024-01-01",
                end_date="2024-01-30",
            )

    @pytest.mark.asyncio
    async def test_account_fetch_failure_falls_back(self):
        """account_trading 调用失败时降级为默认账户。"""
        engine = BacktestEngine(
            kline_fetcher=MockKLineFetcher(),
            benchmark_fetcher=MockBenchmarkFetcher(),
            account_fetcher=MockAccountFetcherFail(),
        )
        strategy = MockStrategy(IDLE_CODE)
        result = await engine.run(
            strategy=strategy,
            stock_code="000001.SZ",
            account_id=99,
            timeframe="1d",
            start_date="2024-01-01",
            end_date="2024-01-30",
        )
        # 默认账户初始资金 1_000_000，无交易则总资产=初始资金
        assert result.bars[-1].total_assets == pytest.approx(1_000_000.0, rel=0.01)

    @pytest.mark.asyncio
    async def test_kline_fetch_failure_raises(self):
        """K 线拉取失败时抛 DataUnavailableError。"""
        class FailKLineFetcher:
            async def fetch_klines(self, *args, **kwargs):
                raise RuntimeError("api_data unavailable")

        engine = BacktestEngine(
            kline_fetcher=FailKLineFetcher(),
            benchmark_fetcher=MockBenchmarkFetcher(),
            account_fetcher=MockAccountFetcher(),
        )
        strategy = MockStrategy(IDLE_CODE)
        from strategy_engine.runtime.engine import DataUnavailableError
        with pytest.raises(DataUnavailableError):
            await engine.run(
                strategy=strategy,
                stock_code="000001.SZ",
                account_id=1,
                timeframe="1d",
                start_date="2024-01-01",
                end_date="2024-01-30",
            )

    @pytest.mark.asyncio
    async def test_benchmark_unavailable_does_not_break(self):
        """基准拉取失败时 benchmark_close=0，回测继续。"""
        class FailBenchmarkFetcher:
            async def fetch_benchmark(self, *args, **kwargs):
                raise RuntimeError("benchmark unavailable")

        engine = BacktestEngine(
            kline_fetcher=MockKLineFetcher(),
            benchmark_fetcher=FailBenchmarkFetcher(),
            account_fetcher=MockAccountFetcher(),
        )
        strategy = MockStrategy(IDLE_CODE)
        result = await engine.run(
            strategy=strategy,
            stock_code="000001.SZ",
            account_id=1,
            timeframe="1d",
            start_date="2024-01-01",
            end_date="2024-01-30",
        )
        # 基准价格全部为 0
        assert all(b.benchmark_close == 0.0 for b in result.bars)
        assert result.total_bars == 30

    @pytest.mark.asyncio
    async def test_empty_klines_returns_empty_result(self):
        """K 线为空时返回空 BacktestResult（total_bars=0）。"""
        class EmptyKLineFetcher:
            async def fetch_klines(self, *args, **kwargs):
                return []

        engine = BacktestEngine(
            kline_fetcher=EmptyKLineFetcher(),
            benchmark_fetcher=MockBenchmarkFetcher(),
            account_fetcher=MockAccountFetcher(),
        )
        strategy = MockStrategy(IDLE_CODE)
        result = await engine.run(
            strategy=strategy,
            stock_code="000001.SZ",
            account_id=1,
            timeframe="1d",
            start_date="2024-01-01",
            end_date="2024-01-30",
        )
        assert result.total_bars == 0
        assert result.bars == []

    @pytest.mark.asyncio
    async def test_total_assets_consistency(self, engine):
        """每个 bar 的 total_assets == cash + sum(positions.market_value)。"""
        strategy = MockStrategy(BUY_SELL_CODE)
        result = await engine.run(
            strategy=strategy,
            stock_code="000001.SZ",
            account_id=1,
            timeframe="1d",
            start_date="2024-01-01",
            end_date="2024-01-30",
        )
        for bar in result.bars:
            positions_value = sum(p.market_value for p in bar.positions)
            expected = round(bar.cash + positions_value, 2)
            assert bar.total_assets == pytest.approx(expected, rel=0.001), \
                f"bar {bar.time} 不一致: total_assets={bar.total_assets}, expected={expected}"


# ============================================================
# set_universe 回归测试（修复 P1-002）
# ============================================================

# 策略 D：在 initialize 中调用 set_universe 改 universe，再 get_history
SET_UNIVERSE_CODE = """
def initialize(context):
    set_universe('601318.SH')  # 改 universe 为与 stock_code 不同的标的


def handle_data(context, data):
    security = context.universe[0] if context.universe else None
    if not security:
        return
    # get_history 用 universe[0]（=601318.SH）查，但 buffer key 是 stock_code
    df = get_history(5, '1d', 'close', security, fq='qfq', include=True)
    if not df or 'close' not in df or len(df['close']) < 2:
        return
    closes = df['close']
    # 价格上穿 MA5 时买入
    ma = sum(closes) / len(closes)
    price = data[security]['close'] if security in data else closes[-1]
    if price > ma and context.portfolio.cash > 10000:
        order_value(security, context.portfolio.cash * 0.5)
        log.info('set_universe 后金叉买入')


def control_risk(context):
    pass
"""


class TestSetUniverseFallback:
    """P1-002 回归：set_universe 改 universe 后，get_history 仍能取到数据。"""

    @pytest.mark.asyncio
    async def test_set_universe_does_not_break_get_history(self, engine):
        """策略调用 set_universe('601318.SH') 后，get_history 应 fallback 到 stock_code 的 buffer。"""
        strategy = MockStrategy(SET_UNIVERSE_CODE)
        result = await engine.run(
            strategy=strategy,
            stock_code="000001.SZ",
            account_id=1,
            timeframe="1d",
            start_date="2024-01-01",
            end_date="2024-01-30",
        )
        # 关键断言：回测完成，未因 get_history 返回空 dict 而完全无交易
        # 即使没有订单（取决于 mock 数据趋势），bars 必须全部处理完
        assert result.total_bars == 30
        # log_entries 中不应出现 get_history 相关的异常
        for bar in result.bars:
            for entry in bar.log_entries:
                assert "get_history" not in entry.lower() or "ERROR" not in entry

    @pytest.mark.asyncio
    async def test_set_universe_strategy_can_produce_orders(self, engine):
        """set_universe 策略在 mock 数据下应能产生订单（fallback 生效，策略能读到历史）。"""
        strategy = MockStrategy(SET_UNIVERSE_CODE)
        result = await engine.run(
            strategy=strategy,
            stock_code="000001.SZ",
            account_id=1,
            timeframe="1d",
            start_date="2024-01-01",
            end_date="2024-01-30",
        )
        total_orders = sum(len(bar.orders) for bar in result.bars)
        # 修复前：get_history 返回空，策略永远 return，订单数 = 0
        # 修复后：fallback 生效，策略能读到历史，在上涨趋势中应产生订单
        assert total_orders > 0, (
            f"set_universe 后策略未产生订单，可能 get_history fallback 失效。"
            f"total_bars={result.total_bars}, total_orders={total_orders}"
        )
