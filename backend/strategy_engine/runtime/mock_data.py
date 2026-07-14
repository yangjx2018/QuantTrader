"""dry-run 用的 mock 数据源。

P1 阶段 api_data 的真实 K 线拉取依赖未就绪（akshare 未安装 / Tushare 未配置），
dry-run 使用固定的 V 形趋势 mock 数据，让用户在策略编辑器中快速验证策略代码
能否正确加载、能否产生订单、是否有运行时异常。

注意：mock 数据仅用于 dry-run。真实回测请走 history_replay 模块的
run_backtest（后续接入 api_data 真实数据）。

数据特征：
- 价格：先下跌 15 天 → 急涨 25 天 → 高位震荡 20 天（共 60 bar）
- 趋势明显，足以触发 4 个内置策略的交易信号
- 时间：从 2024-01-01 起的 60 个交易日（简化为日历日）
"""

from __future__ import annotations

from typing import Optional

from strategy_engine.runtime.engine import (
    AccountConfig,
    KBar,
)


# ============================================================
# 默认账户配置
# ============================================================

DEFAULT_ACCOUNT: AccountConfig = AccountConfig(
    account_id=0,  # dry-run 专用虚拟账户 id
    initial_capital=200_000.0,
    commission_rate=0.001,
)


# ============================================================
# Mock K 线生成
# ============================================================

def _build_v_shape_kbars() -> list[KBar]:
    """生成 60 个交易日的 V 形趋势 K 线。

    阶段 1（1-15）：缓跌 12→9
    阶段 2（16-40）：急涨 9→16.5
    阶段 3（41-60）：高位震荡 16.5±0.5
    """
    prices: list[float] = []
    for i in range(15):
        prices.append(round(12.0 - i * 0.2, 2))
    for i in range(25):
        prices.append(round(9.0 + i * 0.30, 2))
    for i in range(20):
        offset = 0.5 if i % 2 == 0 else -0.5
        prices.append(round(16.5 + offset, 2))

    # 时间从 2024-01-01 起（简化为日历日，跳过每月天数对齐）
    from datetime import date, timedelta
    base = date(2024, 1, 1)
    kbars: list[KBar] = []
    for i, p in enumerate(prices):
        d = (base + timedelta(days=i)).isoformat()
        kbars.append(KBar(
            time=d,
            open=p,
            high=round(p + 0.1, 2),
            low=round(p - 0.1, 2),
            close=p,
            volume=1_000_000,
            amount=round(p * 1_000_000, 2),
        ))
    return kbars


# 完整数据集（60 bar）
V_SHAPE_KBARS: list[KBar] = _build_v_shape_kbars()


# ============================================================
# Mock fetchers（实现 BacktestEngine 需要的 Protocol）
# ============================================================

class MockKLineFetcher:
    """dry-run 专用 K 线源：返回前 max_bars 个 mock bar。"""

    def __init__(self, max_bars: int = 60):
        self.max_bars = max(1, min(int(max_bars), len(V_SHAPE_KBARS)))

    async def fetch_klines(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        timeframe: str,
        fq: Optional[str] = "qfq",
    ) -> list[KBar]:
        # P1 仅支持 1d；mock 数据忽略 start_date/end_date，固定返回前 max_bars 个 bar
        return V_SHAPE_KBARS[: self.max_bars]


class MockBenchmarkFetcher:
    """dry-run 专用基准源：固定价格 3500（让 benchmark_close 字段不为 0）。"""

    async def fetch_benchmark(
        self,
        index_code: str,
        start_date: str,
        end_date: str,
        timeframe: str,
    ) -> dict[str, float]:
        return {kb.time: 3500.0 + i for i, kb in enumerate(V_SHAPE_KBARS[:60])}


class MockAccountFetcher:
    """dry-run 专用账户源：返回固定的 200 万 / 0.1%。"""

    async def fetch_account(self, account_id: int) -> Optional[AccountConfig]:
        # dry-run 忽略 account_id，始终返回默认账户
        return AccountConfig(
            account_id=account_id,
            initial_capital=DEFAULT_ACCOUNT.initial_capital,
            commission_rate=DEFAULT_ACCOUNT.commission_rate,
        )


__all__ = [
    "DEFAULT_ACCOUNT",
    "V_SHAPE_KBARS",
    "MockKLineFetcher",
    "MockBenchmarkFetcher",
    "MockAccountFetcher",
]
