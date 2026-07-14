"""回测引擎数据契约类型。

字段与 history_replay.strategy_mock 中的 BacktestResult/BarRecord/OrderRecord/PositionRecord
完全对齐 —— history_replay/calculator.py 直接消费这些字段计算指标。

跨模块契约：strategy_engine 必须产出这些类型；history_replay 是消费方。
后续联调阶段 history_replay 将从本模块 import 类型（替代其本地定义）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OrderRecord:
    """单笔订单记录（订单已撮合后的快照）。"""

    time: str           # 成交日期 YYYY-MM-DD
    side: str           # "buy" / "sell"
    price: float        # 成交价（每股）
    quantity: int       # 成交数量（股数）
    amount: float       # 成交金额 = price × quantity
    commission: float   # 手续费
    pnl: float          # 卖出时为已实现盈亏，买入为 0
    signal: str         # 触发信号名称


@dataclass
class PositionRecord:
    """单个持仓快照（某交易日收盘时）。"""

    stock_code: str
    quantity: int
    cost_price: float
    current_price: float
    market_value: float
    floating_pnl: float


@dataclass
class BarRecord:
    """单根 K 线 bar 的完整记录。

    一个 bar 包含：行情 + 策略信号 + 当日订单 + 收盘持仓 + 账户快照 + 日志。
    history_replay/calculator.py 基于此计算所有报告指标。
    """

    # 行情
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float

    # 基准指数（沪深300 = 000300.SH）
    benchmark_close: float = 0.0

    # 策略信号（当 bar 触发的最新信号）
    signal: Optional[str] = None          # "buy" / "sell" / None
    signal_reason: Optional[str] = None   # 触发原因

    # 订单
    orders: list[OrderRecord] = field(default_factory=list)

    # 收盘持仓
    positions: list[PositionRecord] = field(default_factory=list)

    # 账户快照
    cash: float = 0.0
    total_assets: float = 0.0

    # 日志
    log_entries: list[str] = field(default_factory=list)


@dataclass
class BacktestResult:
    """一次完整回测的结果。

    包含基础信息 + 逐 bar 明细。**不包含任何统计指标** ——
    指标由 history_replay/calculator.py 计算（职责分离）。
    """

    # 基础信息
    session_id: str
    stock_code: str
    strategy_id: int
    strategy_name: str
    account_id: int
    timeframe: str
    start_date: str
    end_date: str
    total_bars: int
    time_elapsed: float

    # 逐 bar 明细
    bars: list[BarRecord] = field(default_factory=list)


__all__ = [
    "OrderRecord",
    "PositionRecord",
    "BarRecord",
    "BacktestResult",
]
