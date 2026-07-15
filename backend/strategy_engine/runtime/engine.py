"""回测引擎主循环：拉数据 → 加载策略 → 逐 bar 驱动 → 撮合 → 产出 BacktestResult。

设计要点：
- 引擎不直接 import api_data / account_trading，通过 Protocol 注入依赖
- 用户代码异常翻译为日志，不中断回测
- BacktestResult 严格对齐 history_replay.strategy_mock 字段（运行时类型契约）

主循环顺序（每 bar）：
    更新 context.current_dt/price
  → before_trading_start (可选)
  → reset_pending_orders
  → handle_data (收集订单)
  → 撮合（按当 bar 收盘价）
  → control_risk (可选)
  → 记录 BarRecord
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from strategy_engine.runtime.api import DSLLogger
from strategy_engine.runtime.context import Portfolio, Position, StrategyContext
from strategy_engine.runtime.loader import (
    InvalidStrategyError,
    StrategyInstance,
    StrategyLoadError,
    StrategyLoader,
    StrategyTimeoutError,
)
from strategy_engine.runtime.types import (
    BacktestResult,
    BarRecord,
    OrderRecord,
    PositionRecord,
)


# ============================================================
# 依赖协议（依赖注入，避免硬耦合 api_data / account_trading）
# ============================================================

@dataclass
class KBar:
    """单根 K 线（引擎工作单元）。"""

    time: str           # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    amount: float = 0.0


@dataclass
class AccountConfig:
    """虚拟账户配置（由调用方提供）。"""

    account_id: int
    initial_capital: float
    commission_rate: float = 0.001   # 默认 0.1%
    slippage: float = 0.0            # 滑点（P1 简化为 0）


class KLineFetcher(Protocol):
    """K 线拉取协议。"""

    async def fetch_klines(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        timeframe: str,
        fq: Optional[str] = "qfq",
    ) -> list[KBar]: ...


class BenchmarkFetcher(Protocol):
    """基准指数拉取协议。"""

    async def fetch_benchmark(
        self,
        index_code: str,        # 默认 "000300.SH" 沪深300
        start_date: str,
        end_date: str,
        timeframe: str,
    ) -> dict[str, float]:      # {date_str: close_price}
        ...


class AccountFetcher(Protocol):
    """虚拟账户查询协议。"""

    async def fetch_account(self, account_id: int) -> Optional[AccountConfig]: ...


# ============================================================
# 异常
# ============================================================

class BacktestError(Exception):
    """回测引擎基础异常。"""


class StrategyNotActive(BacktestError):
    """策略未启用（status != 'active'）。"""


class DataUnavailableError(BacktestError):
    """行情数据不可用。"""


class StrategyRuntimeError(BacktestError):
    """策略运行时异常（已翻译，含 bar 时间与原始异常摘要）。"""


# ============================================================
# 引擎
# ============================================================

# 默认基准指数代码
DEFAULT_BENCHMARK_CODE: str = "000300.SH"

# 默认手续费下限（A 股最低 5 元/笔）
DEFAULT_MIN_COMMISSION: float = 5.0


class BacktestEngine:
    """回测引擎。

    通过依赖注入接收 K 线 / 基准 / 账户的数据源，便于测试与跨模块解耦。
    引擎本身无状态，可被并发调用（每次调用产生独立的 context）。

    用法：
        engine = BacktestEngine(kline_fetcher, benchmark_fetcher, account_fetcher)
        result = await engine.run(
            strategy=strategy_obj,  # 含 code_content / parameters / name
            stock_code="000001.SZ",
            account_id=1,
            timeframe="1d",
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
    """

    def __init__(
        self,
        kline_fetcher: KLineFetcher,
        benchmark_fetcher: BenchmarkFetcher,
        account_fetcher: AccountFetcher,
        benchmark_code: str = DEFAULT_BENCHMARK_CODE,
        min_commission: float = DEFAULT_MIN_COMMISSION,
    ) -> None:
        self.kline_fetcher = kline_fetcher
        self.benchmark_fetcher = benchmark_fetcher
        self.account_fetcher = account_fetcher
        self.benchmark_code = benchmark_code
        self.min_commission = min_commission

    async def run(
        self,
        strategy,            # Strategy 主档对象，需含 code_content / parameters / name / status
        stock_code: str,
        account_id: int,
        timeframe: str,
        start_date: str,
        end_date: str,
        session_id: Optional[str] = None,
    ) -> BacktestResult:
        """执行完整回测。"""
        # === 1. 前置校验 ===
        if strategy is None:
            raise BacktestError("策略不存在")
        if getattr(strategy, "status", None) != "active":
            raise StrategyNotActive(
                f"策略状态非 active，当前 status={getattr(strategy, 'status', 'unknown')}"
            )
        if not getattr(strategy, "code_content", None):
            raise InvalidStrategyError("策略代码为空")

        if timeframe != "1d":
            raise BacktestError(f"P1 仅支持 timeframe='1d'，当前={timeframe}")

        # === 2. 并发拉取数据 ===
        account, kbars, benchmarks = await self._fetch_all(
            account_id, stock_code, start_date, end_date, timeframe
        )

        if not kbars:
            return self._empty_result(
                strategy, stock_code, account_id, timeframe, start_date, end_date, session_id
            )

        # === 3. 加载策略 + 初始化 context ===
        log_sink: list[str] = []
        portfolio = Portfolio(
            initial_capital=account.initial_capital,
            cash=account.initial_capital,
            commission_rate=account.commission_rate,
        )
        context = StrategyContext(portfolio=portfolio, universe=[stock_code])

        # 历史 K 线缓冲：策略通过 get_history 读到这里
        # 结构：{security: {"close": [...], "open": [...], "high": [...], "low": [...], "volume": [...], "amount": [...]}}
        # 引擎在每个 bar 处理前把当前 K 线追加进去
        history_buffer: dict[str, dict[str, list]] = {
            stock_code: {"open": [], "high": [], "low": [], "close": [],
                          "volume": [], "amount": []}
        }

        loader = StrategyLoader()

        def history_loader(n: int, unit: str = "1d", fields=None,
                            security: Optional[str] = None,
                            fq: Optional[str] = "qfq", include: bool = False,
                            context=None, **kwargs) -> dict[str, list]:
            """策略 get_history 的实现：从引擎累积的 K 线 buffer 取数。"""
            if security is None:
                if not context or not context.universe:
                    return {}
                security = context.universe[0]
            buf = history_buffer.get(security)
            if not buf:
                # fallback：用户 set_universe 改了标的，但 buffer 按原始 stock_code 存
                # 取第一个 buffer（即 stock_code 对应的数据），避免 get_history 取不到数据
                if history_buffer:
                    buf = next(iter(history_buffer.values()))
                else:
                    return {}

            # 字段标准化为 list
            if fields is None:
                field_list = ["close"]
            elif isinstance(fields, str):
                field_list = [fields]
            else:
                field_list = list(fields)

            # 取最近 n 条（include=False 排除当前 bar，即最后一项）
            closes = buf.get("close", [])
            available = len(closes) - (0 if include else 1)
            take = min(int(n), max(available, 0))
            start = available - take if available > 0 else 0

            result: dict[str, list] = {}
            for f in field_list:
                series = buf.get(f, [])
                # 切片 [start : start + take]
                result[f] = list(series[start:start + take]) if take > 0 else []
            return result

        try:
            instance: StrategyInstance = loader.load(
                strategy.code_content,
                parameters=getattr(strategy, "parameters", None) or {},
                context=context,
                log_sink=log_sink,
                history_loader=history_loader,
            )
        except (StrategyLoadError, InvalidStrategyError):
            raise
        except Exception as e:
            raise BacktestError(f"策略加载失败: {type(e).__name__}: {e}") from e

        # === 4. 调用 initialize ===
        try:
            instance.call("initialize", context)
        except StrategyTimeoutError as e:
            raise BacktestError(f"initialize 超时: {e}") from e
        except Exception as e:
            raise BacktestError(f"initialize 异常: {type(e).__name__}: {e}") from e

        # === 5. 逐 bar 主循环 ===
        start_ts = time.perf_counter()
        bars_result: list[BarRecord] = []

        for i, kbar in enumerate(kbars):
            # 追加当前 bar 到 history_buffer（在钩子调用前，策略 get_history 含当前可读到）
            buf_entry = history_buffer[stock_code]
            buf_entry["open"].append(kbar.open)
            buf_entry["high"].append(kbar.high)
            buf_entry["low"].append(kbar.low)
            buf_entry["close"].append(kbar.close)
            buf_entry["volume"].append(kbar.volume)
            buf_entry["amount"].append(kbar.amount)

            bar_record = self._process_bar(
                kbar=kbar,
                benchmark_close=benchmarks.get(kbar.time, 0.0),
                context=context,
                instance=instance,
                stock_code=stock_code,
                log_sink=log_sink,
                history_loader=history_loader,
            )
            bars_result.append(bar_record)
            # 每 bar 重置日志 sink（已经写到 BarRecord.log_entries）
            log_sink.clear()

        elapsed = time.perf_counter() - start_ts

        return BacktestResult(
            session_id=session_id or f"local_{strategy.id}_{stock_code}",
            stock_code=stock_code,
            strategy_id=strategy.id,
            strategy_name=strategy.name,
            account_id=account_id,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            total_bars=len(bars_result),
            time_elapsed=round(elapsed, 3),
            bars=bars_result,
        )

    # ------------------------------------------------------------
    # 内部：拉取数据（并发）
    # ------------------------------------------------------------

    async def _fetch_all(
        self,
        account_id: int,
        stock_code: str,
        start_date: str,
        end_date: str,
        timeframe: str,
    ) -> tuple[AccountConfig, list[KBar], dict[str, float]]:
        """并发拉取账户、K 线、基准。账户失败降级；K 线失败抛错；基准失败置 0。"""

        async def _get_account() -> AccountConfig:
            try:
                acc = await self.account_fetcher.fetch_account(account_id)
                if acc is None:
                    return AccountConfig(
                        account_id=account_id,
                        initial_capital=1_000_000.0,
                        commission_rate=0.001,
                    )
                return acc
            except Exception:
                # 降级
                return AccountConfig(
                    account_id=account_id,
                    initial_capital=1_000_000.0,
                    commission_rate=0.001,
                )

        async def _get_klines() -> list[KBar]:
            try:
                return await self.kline_fetcher.fetch_klines(
                    stock_code, start_date, end_date, timeframe, fq="qfq"
                )
            except Exception as e:
                raise DataUnavailableError(
                    f"K 线拉取失败: {stock_code} {start_date}~{end_date} {timeframe}: {e}"
                ) from e

        async def _get_benchmark() -> dict[str, float]:
            try:
                return await self.benchmark_fetcher.fetch_benchmark(
                    self.benchmark_code, start_date, end_date, timeframe
                )
            except Exception:
                # 基准不可用：返回空 dict，引擎写 benchmark_close=0
                return {}

        account, kbars, benchmarks = await asyncio.gather(
            _get_account(), _get_klines(), _get_benchmark()
        )
        return account, kbars, benchmarks

    # ------------------------------------------------------------
    # 内部：单 bar 处理
    # ------------------------------------------------------------

    def _process_bar(
        self,
        kbar: KBar,
        benchmark_close: float,
        context: StrategyContext,
        instance: StrategyInstance,
        stock_code: str,
        log_sink: list[str],
        history_loader=None,
    ) -> BarRecord:
        """处理单根 bar：钩子调度 + 撮合 + 记录。"""
        # 1. 更新 context
        context.current_dt = kbar.time
        context.current_price = kbar.close
        context.portfolio.update_current_price(stock_code, kbar.close)
        context.reset_pending_orders()

        # 2. before_trading_start（可选）
        if instance.has_hook("before_trading_start"):
            self._safe_call(
                instance, "before_trading_start",
                args=(context,),
                log_sink=log_sink, bar_time=kbar.time,
            )

        # 3. handle_data（必须）
        data = {
            stock_code: {
                "open": kbar.open,
                "high": kbar.high,
                "low": kbar.low,
                "close": kbar.close,
                "volume": kbar.volume,
                "amount": kbar.amount,
            }
        }
        self._safe_call(
            instance, "handle_data",
            args=(context, data),
            log_sink=log_sink, bar_time=kbar.time,
        )

        # 4. 撮合 pending_orders
        orders_filled: list[OrderRecord] = []
        signal_for_bar: Optional[str] = None
        signal_reason: Optional[str] = None

        for pending in context.pending_orders:
            order_rec, signal_label = self._fill_order(
                pending, kbar, context.portfolio, stock_code
            )
            if order_rec is not None:
                orders_filled.append(order_rec)
                if signal_for_bar is None and signal_label:
                    signal_for_bar = order_rec.side
                    signal_reason = signal_label

        # 5. control_risk（可选）
        if instance.has_hook("control_risk"):
            self._safe_call(
                instance, "control_risk",
                args=(context,),
                log_sink=log_sink, bar_time=kbar.time,
            )

        # 6. 构造 BarRecord
        positions_snapshot = [
            PositionRecord(
                stock_code=sec,
                quantity=pos.quantity,
                cost_price=pos.cost_price,
                current_price=pos.current_price,
                market_value=pos.market_value,
                floating_pnl=pos.floating_pnl,
            )
            for sec, pos in context.portfolio.positions.items()
        ]

        return BarRecord(
            time=kbar.time,
            open=kbar.open,
            high=kbar.high,
            low=kbar.low,
            close=kbar.close,
            volume=kbar.volume,
            amount=kbar.amount,
            benchmark_close=benchmark_close,
            signal=signal_for_bar,
            signal_reason=signal_reason,
            orders=orders_filled,
            positions=positions_snapshot,
            cash=context.portfolio.cash,
            total_assets=context.portfolio.total_value,
            log_entries=list(log_sink),
        )

    # ------------------------------------------------------------
    # 内部：钩子异常翻译
    # ------------------------------------------------------------

    def _safe_call(
        self,
        instance: StrategyInstance,
        hook_name: str,
        args: tuple,
        log_sink: list[str],
        bar_time: str,
    ) -> None:
        """调用钩子，捕获异常翻译为日志。不中断回测。"""
        try:
            instance.call(hook_name, *args)
        except StrategyTimeoutError as e:
            log_sink.append(
                f"[ERROR] {hook_name} 超时 @ {bar_time}: {e}"
            )
        except Exception as e:
            log_sink.append(
                f"[ERROR] {hook_name} 运行时异常 @ {bar_time}: "
                f"{type(e).__name__}: {e}"
            )

    # ------------------------------------------------------------
    # 内部：撮合
    # ------------------------------------------------------------

    def _fill_order(
        self,
        pending: dict,
        kbar: KBar,
        portfolio: Portfolio,
        stock_code: str,
    ) -> tuple[Optional[OrderRecord], Optional[str]]:
        """根据 pending_order 的 mode 计算实际股数并撮合。

        Returns:
            (OrderRecord | None, signal_label | None)
            OrderRecord=None 表示订单被拒（拒单原因已写入策略 log）
        """
        security = pending["security"]
        side = pending["side"]
        mode = pending["mode"]
        target = pending["target"]

        # 1. 根据 mode 算出实际目标股数
        actual_qty, resolved_side, signal_label = self._resolve_quantity(
            security=security,
            side=side,
            mode=mode,
            target=target,
            portfolio=portfolio,
            current_price=kbar.close,
        )

        if actual_qty == 0:
            return None, signal_label

        # 2. 计算手续费
        amount = actual_qty * kbar.close
        commission = max(amount * portfolio.commission_rate, self.min_commission)

        # 3. 调用 Portfolio 撮合
        try:
            if resolved_side == "buy":
                if portfolio.cash < amount + commission:
                    return None, signal_label  # 现金不足拒单（静默）
                portfolio._apply_buy(security, actual_qty, kbar.close, commission)
                pnl = 0.0
            else:  # sell
                pnl = portfolio._apply_sell(security, actual_qty, kbar.close, commission)
        except ValueError:
            # 持仓不足等拒单（静默，引擎不输出日志，策略如需自行处理可读 context）
            return None, signal_label

        return OrderRecord(
            time=kbar.time,
            side=resolved_side,
            price=kbar.close,
            quantity=actual_qty,
            amount=round(amount, 2),
            commission=round(commission, 2),
            pnl=round(pnl, 2),
            signal=signal_label or resolved_side,
        ), signal_label

    def _resolve_quantity(
        self,
        security: str,
        side: str,
        mode: str,
        target: float,
        portfolio: Portfolio,
        current_price: float,
    ) -> tuple[int, str, Optional[str]]:
        """根据订单模式计算实际股数与方向。

        Returns:
            (quantity, resolved_side, signal_label)
            quantity=0 表示拒单（数量不足 100 股、目标股数小于持仓等）
        """
        if current_price <= 0:
            return 0, side, None

        if mode == "quantity":
            qty = int(target)
            if qty <= 0:
                return 0, side, None
            if side == "buy":
                # 买入必须 100 整数倍
                qty = (qty // 100) * 100
                if qty == 0:
                    return 0, side, None
            return qty, side, None

        if mode == "value":
            value = float(target)
            if value <= 0:
                return 0, side, None
            if side == "buy":
                # 按金额反算股数，向下取整到 100 倍
                raw_qty = value / current_price
                qty = (int(raw_qty) // 100) * 100
                if qty == 0:
                    return 0, side, None
                return qty, "buy", None
            else:  # sell
                # 卖出按金额反算股数（向下取整，不强求 100 倍）
                qty = int(value / current_price)
                if qty <= 0:
                    return 0, "sell", None
                return qty, "sell", None

        if mode == "target_quantity":
            target_qty = int(target)
            pos = portfolio.get_position(security)
            current_qty = pos.quantity if pos else 0
            diff = target_qty - current_qty
            if diff > 0:
                qty = (diff // 100) * 100
                if qty == 0:
                    return 0, "buy", None
                return qty, "buy", "调仓买入"
            elif diff < 0:
                return -diff, "sell", "调仓卖出"
            else:
                return 0, "buy", None

        if mode == "target_value":
            target_value = float(target)
            pos = portfolio.get_position(security)
            current_value = (pos.quantity * current_price) if pos else 0.0
            diff_value = target_value - current_value
            if abs(diff_value) < current_price:
                # 差额小于 1 股，跳过
                return 0, "buy", None
            if diff_value > 0:
                raw_qty = diff_value / current_price
                qty = (int(raw_qty) // 100) * 100
                if qty == 0:
                    return 0, "buy", None
                return qty, "buy", "调仓买入"
            else:
                qty = int(-diff_value / current_price)
                if qty <= 0:
                    return 0, "sell", None
                return qty, "sell", "调仓卖出"

        return 0, side, None

    # ------------------------------------------------------------
    # 内部：空回测结果
    # ------------------------------------------------------------

    def _empty_result(
        self,
        strategy,
        stock_code: str,
        account_id: int,
        timeframe: str,
        start_date: str,
        end_date: str,
        session_id: Optional[str],
    ) -> BacktestResult:
        return BacktestResult(
            session_id=session_id or f"local_{strategy.id}_{stock_code}_empty",
            stock_code=stock_code,
            strategy_id=strategy.id,
            strategy_name=strategy.name,
            account_id=account_id,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            total_bars=0,
            time_elapsed=0.0,
            bars=[],
        )


__all__ = [
    "BacktestEngine",
    "BacktestError",
    "StrategyNotActive",
    "DataUnavailableError",
    "StrategyRuntimeError",
    "KBar",
    "AccountConfig",
    "KLineFetcher",
    "BenchmarkFetcher",
    "AccountFetcher",
    "DEFAULT_BENCHMARK_CODE",
    "DEFAULT_MIN_COMMISSION",
]
