"""实盘/模拟执行：单次 tick 驱动策略引擎并产出订单意图。

复用 strategy_engine 的 StrategyLoader / handle_data / 数量解析逻辑，
但不在引擎内撮合，而是把意图交给 order_bridge → account_trading。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from strategy_engine.runtime.context import Portfolio, Position, StrategyContext
from strategy_engine.runtime.engine import BacktestEngine, KBar
from strategy_engine.runtime.loader import StrategyInstance, StrategyLoader
from strategy_engine.runtime.real_data import RealKLineFetcher
from strategy_engine.service import load_strategy

logger = logging.getLogger(__name__)


@dataclass
class OrderIntent:
    security: str
    side: str
    quantity: int
    price: float
    signal_label: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class TickResult:
    bar_time: str
    price: float
    intents: list[OrderIntent] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: Optional[str] = None


@dataclass
class ExecutionRuntime:
    """单个执行实例的内存态（worker 持有）。"""

    execution_id: int
    strategy_id: int
    account_id: int
    symbol: str
    timeframe: str = "1d"
    params: dict[str, Any] = field(default_factory=dict)
    instance: Optional[StrategyInstance] = None
    context: Optional[StrategyContext] = None
    history_buffer: dict[str, dict[str, list]] = field(default_factory=dict)
    last_processed_bar: Optional[str] = None
    # 实盘：记录已下单的 bar，禁止同 bar 重复委托
    last_ordered_bar: Optional[str] = None
    initialized: bool = False
    log_sink: list[str] = field(default_factory=list)


def _kbar_time(kbar: KBar) -> str:
    return kbar.time[:10] if kbar.time else ""


def _build_portfolio_from_snapshots(
    cash: float,
    positions: list[dict[str, Any]],
    commission_rate: float = 0.001,
) -> Portfolio:
    portfolio = Portfolio(
        initial_capital=cash,
        cash=cash,
        commission_rate=commission_rate,
    )
    for row in positions:
        symbol = str(row.get("symbol") or "")
        qty = int(row.get("quantity") or row.get("available_quantity") or 0)
        if not symbol or qty <= 0:
            continue
        cost = float(row.get("cost_price") or row.get("avg_cost") or 0)
        price = float(row.get("current_price") or row.get("last_price") or cost or 0)
        portfolio.positions[symbol] = Position(
            security=symbol,
            quantity=qty,
            cost_price=cost,
            current_price=price,
        )
    return portfolio


async def load_account_portfolio(db: AsyncSession, account_id: int) -> tuple[Portfolio, Any]:
    """从 account_trading 拉余额/持仓，构造策略 Portfolio。"""
    from account_trading.repository import AccountTradingRepository

    repo = AccountTradingRepository(db)
    account = await repo.get_account(account_id)
    if account is None:
        raise ValueError(f"交易账户不存在: {account_id}")

    balance = await repo.get_latest_balance(account) or {}
    positions = await repo.list_latest_positions(account) or []
    cash = float(
        balance.get("available_cash")
        or balance.get("cash_balance")
        or (account.meta_json or {}).get("initial_cash")
        or 1_000_000
    )
    commission = float((account.meta_json or {}).get("commission_rate") or 0.001)
    return _build_portfolio_from_snapshots(cash, positions, commission), account


async def fetch_recent_bars(
    db: AsyncSession,
    symbol: str,
    timeframe: str = "1d",
    lookback_days: int = 120,
) -> list[KBar]:
    end = datetime.now().date()
    start = end - timedelta(days=lookback_days)
    fetcher = RealKLineFetcher(db)
    return await fetcher.fetch_klines(
        stock_code=symbol,
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
        timeframe=timeframe,
    )


def _make_history_loader(history_buffer: dict[str, dict[str, list]], default_symbol: str):
    def history_loader(
        n: int,
        unit: str = "1d",
        fields=None,
        security: Optional[str] = None,
        fq: Optional[str] = "qfq",
        include: bool = False,
        context=None,
        **kwargs,
    ) -> dict[str, list]:
        if security is None:
            if context and getattr(context, "universe", None):
                security = context.universe[0]
            else:
                security = default_symbol
        buf = history_buffer.get(security) or (
            next(iter(history_buffer.values())) if history_buffer else None
        )
        if not buf:
            return {}
        if fields is None:
            field_list = ["close"]
        elif isinstance(fields, str):
            field_list = [fields]
        else:
            field_list = list(fields)
        closes = buf.get("close", [])
        available = len(closes) - (0 if include else 1)
        take = min(int(n), max(available, 0))
        start = available - take if available > 0 else 0
        return {f: list(buf.get(f, [])[start : start + take]) if take > 0 else [] for f in field_list}

    return history_loader


async def ensure_runtime_ready(
    db: AsyncSession,
    runtime: ExecutionRuntime,
) -> None:
    """首次 tick：加载策略、warmup 历史、initialize。"""
    if runtime.initialized and runtime.instance and runtime.context:
        # 刷新账户快照到 portfolio
        portfolio, _ = await load_account_portfolio(db, runtime.account_id)
        runtime.context.portfolio = portfolio
        return

    bars = await fetch_recent_bars(db, runtime.symbol, runtime.timeframe)
    if len(bars) < 2:
        raise ValueError(f"标的 {runtime.symbol} K 线不足，无法启动策略")

    portfolio, _ = await load_account_portfolio(db, runtime.account_id)
    context = StrategyContext(portfolio=portfolio, universe=[runtime.symbol])
    history_buffer: dict[str, dict[str, list]] = {
        runtime.symbol: {"open": [], "high": [], "low": [], "close": [], "volume": [], "amount": []}
    }
    log_sink: list[str] = []
    history_loader = _make_history_loader(history_buffer, runtime.symbol)

    # 优先用 registry 缓存的实例；再按代码重新绑定 context
    cached = await load_strategy(db, runtime.strategy_id)
    from strategy_engine.repository import StrategyRepository

    repo = StrategyRepository(db)
    strategy = await repo.get_by_id(runtime.strategy_id)
    if not strategy or not strategy.code_content:
        raise ValueError(f"策略 {runtime.strategy_id} 不可用")

    merged_params = {**(strategy.parameters or {}), **(runtime.params or {})}
    # 标的参数不进 g
    strategy_params = {k: v for k, v in merged_params.items() if k not in {"symbol", "timeframe", "interval_sec", "lookback_days"}}

    loader = StrategyLoader()
    instance = loader.load(
        code_content=strategy.code_content,
        parameters=strategy_params,
        context=context,
        log_sink=log_sink,
        history_loader=history_loader,
    )
    # 保留缓存校验结果
    _ = cached

    instance.call("initialize", context)

    # warmup：静默重放历史 bar 的 handle_data，恢复 g.* 等策略状态，但不下单
    for kbar in bars[:-1]:
        buf = history_buffer[runtime.symbol]
        buf["open"].append(kbar.open)
        buf["high"].append(kbar.high)
        buf["low"].append(kbar.low)
        buf["close"].append(kbar.close)
        buf["volume"].append(kbar.volume)
        buf["amount"].append(kbar.amount)

        context.current_dt = _kbar_time(kbar)
        context.current_price = kbar.close
        context.portfolio.update_current_price(runtime.symbol, kbar.close)
        context.reset_pending_orders()
        log_sink.clear()
        try:
            if instance.has_hook("before_trading_start"):
                instance.call("before_trading_start", context)
            instance.call("handle_data", context, {runtime.symbol: kbar.close})
            if instance.has_hook("control_risk"):
                instance.call("control_risk", context)
        except Exception as exc:
            logger.warning(
                "execution %s warmup bar %s failed: %s",
                runtime.execution_id,
                context.current_dt,
                exc,
            )
        # 关键丢弃 warmup 产生的订单意图
        context.reset_pending_orders()

    runtime.instance = instance
    runtime.context = context
    runtime.history_buffer = history_buffer
    runtime.log_sink = log_sink
    runtime.last_processed_bar = _kbar_time(bars[-2]) if len(bars) >= 2 else None
    runtime.initialized = True
    logger.info(
        "execution %s runtime ready: strategy=%s symbol=%s warmup_bars=%s (state replayed)",
        runtime.execution_id,
        runtime.strategy_id,
        runtime.symbol,
        len(bars) - 1,
    )


def _resolve_intents(
    pending_orders: list[dict],
    portfolio: Portfolio,
    price: float,
    default_symbol: str,
) -> list[OrderIntent]:
    engine = BacktestEngine(
        kline_fetcher=None,  # type: ignore[arg-type]
        benchmark_fetcher=None,  # type: ignore[arg-type]
        account_fetcher=None,  # type: ignore[arg-type]
    )
    intents: list[OrderIntent] = []
    for pending in pending_orders:
        security = pending.get("security") or default_symbol
        side = pending.get("side") or "buy"
        mode = pending.get("mode") or "quantity"
        target = float(pending.get("target") or 0)
        qty, resolved_side, signal_label = engine._resolve_quantity(
            security=security,
            side=side,
            mode=mode,
            target=target,
            portfolio=portfolio,
            current_price=price,
        )
        if qty <= 0:
            continue
        intents.append(
            OrderIntent(
                security=security,
                side=resolved_side,
                quantity=qty,
                price=price,
                signal_label=signal_label or resolved_side,
                reason=f"strategy:{mode}",
            )
        )
    return intents


async def run_tick(db: AsyncSession, runtime: ExecutionRuntime) -> TickResult:
    """执行一次策略 tick：有新 bar 才跑 handle_data。"""
    await ensure_runtime_ready(db, runtime)
    assert runtime.instance and runtime.context

    bars = await fetch_recent_bars(db, runtime.symbol, runtime.timeframe)
    if not bars:
        return TickResult(bar_time="", price=0, skipped=True, skip_reason="无K线数据")

    latest = bars[-1]
    bar_time = _kbar_time(latest)
    if runtime.last_processed_bar and bar_time <= runtime.last_processed_bar:
        return TickResult(
            bar_time=bar_time,
            price=latest.close,
            skipped=True,
            skip_reason=f"K线未更新 (last={runtime.last_processed_bar})",
        )

    # 刷新账户组合
    portfolio, _ = await load_account_portfolio(db, runtime.account_id)
    runtime.context.portfolio = portfolio
    runtime.context.current_dt = bar_time
    runtime.context.current_price = latest.close
    runtime.context.portfolio.update_current_price(runtime.symbol, latest.close)
    runtime.context.reset_pending_orders()
    runtime.log_sink.clear()

    buf = runtime.history_buffer.setdefault(
        runtime.symbol,
        {"open": [], "high": [], "low": [], "close": [], "volume": [], "amount": []},
    )
    # 若 warmup 后 buffer 已含到 last_processed，只追加新 bar
    if not buf["close"] or len(buf["close"]) < len(bars):
        # 对齐：用全量重建到 latest（含）
        for key in buf:
            buf[key].clear()
        for kbar in bars:
            buf["open"].append(kbar.open)
            buf["high"].append(kbar.high)
            buf["low"].append(kbar.low)
            buf["close"].append(kbar.close)
            buf["volume"].append(kbar.volume)
            buf["amount"].append(kbar.amount)
    else:
        buf["open"].append(latest.open)
        buf["high"].append(latest.high)
        buf["low"].append(latest.low)
        buf["close"].append(latest.close)
        buf["volume"].append(latest.volume)
        buf["amount"].append(latest.amount)

    data = {runtime.symbol: latest.close}
    try:
        if runtime.instance.has_hook("before_trading_start"):
            runtime.instance.call("before_trading_start", runtime.context)
        runtime.instance.call("handle_data", runtime.context, data)
        if runtime.instance.has_hook("control_risk"):
            runtime.instance.call("control_risk", runtime.context)
    except Exception as exc:
        logger.exception("execution %s strategy tick failed", runtime.execution_id)
        runtime.last_processed_bar = bar_time
        return TickResult(
            bar_time=bar_time,
            price=latest.close,
            logs=[*runtime.log_sink, f"[ERROR] {type(exc).__name__}: {exc}"],
            skipped=True,
            skip_reason=f"策略运行异常: {exc}",
        )

    intents = _resolve_intents(
        runtime.context.pending_orders,
        runtime.context.portfolio,
        latest.close,
        runtime.symbol,
    )
    runtime.last_processed_bar = bar_time
    return TickResult(
        bar_time=bar_time,
        price=latest.close,
        intents=intents,
        logs=list(runtime.log_sink),
    )
