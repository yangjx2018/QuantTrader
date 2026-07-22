"""策略执行后台 Worker：轮询 running 实例并驱动 live_runner → order_bridge。"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from common.database import async_session
from strategy_execution.live_runner import ExecutionRuntime, run_tick
from strategy_execution.models import Execution, ExecutionLog, ExecutionSignal, RiskAlert, RiskRule
from strategy_execution.order_bridge import place_execution_order

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SEC = 30


class ExecutionWorker:
    """全局单例后台循环。"""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._runtimes: dict[int, ExecutionRuntime] = {}
        self._lock = asyncio.Lock()
        self._tick_locks: dict[int, asyncio.Lock] = {}

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _tick_lock_for(self, execution_id: int) -> asyncio.Lock:
        lock = self._tick_locks.get(execution_id)
        if lock is None:
            lock = asyncio.Lock()
            self._tick_locks[execution_id] = lock
        return lock

    async def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="execution-worker")
        logger.info("ExecutionWorker started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        self._runtimes.clear()
        self._tick_locks.clear()
        logger.info("ExecutionWorker stopped")

    async def register(self, execution: Execution) -> None:
        params = dict(execution.params or {})
        symbol = str(params.get("symbol") or "000001.SZ")
        timeframe = str(params.get("timeframe") or "1d")
        try:
            strategy_id = int(execution.strategy_id)
        except (TypeError, ValueError):
            strategy_id = int(str(execution.strategy_id))

        async with self._lock:
            existing = self._runtimes.get(execution.id)
            if existing is not None and existing.initialized:
                existing.symbol = symbol
                existing.timeframe = timeframe
                existing.params = params
                existing.account_id = int(execution.account_id)
                existing.strategy_id = strategy_id
                logger.info("re-registered execution %s (kept runtime state)", execution.id)
                return

            runtime = ExecutionRuntime(
                execution_id=execution.id,
                strategy_id=strategy_id,
                account_id=int(execution.account_id),
                symbol=symbol,
                timeframe=timeframe,
                params=params,
            )
            self._runtimes[execution.id] = runtime
            self._tick_locks.setdefault(execution.id, asyncio.Lock())
        logger.info("registered execution %s symbol=%s", execution.id, symbol)

    async def unregister(self, execution_id: int) -> None:
        async with self._lock:
            self._runtimes.pop(execution_id, None)
            self._tick_locks.pop(execution_id, None)

    async def force_tick(self, execution_id: int, *, allow_same_bar: bool | None = None) -> dict:
        """手动触发一次 tick。

        allow_same_bar:
          - None：paper 允许同 bar 再跑；live 禁止（避免重复下单）
          - True/False：显式覆盖
        """
        async with self._lock:
            runtime = self._runtimes.get(execution_id)

        account_type: str | None = None
        if runtime is None:
            async with async_session() as db:
                execution = await db.get(Execution, execution_id)
                if not execution or execution.status != "running":
                    raise ValueError("执行实例不存在或未在运行")
                await self.register(execution)
                try:
                    from account_trading.repository import AccountTradingRepository

                    repo = AccountTradingRepository(db)
                    acc = await repo.get_account(int(execution.account_id))
                    account_type = acc.account_type if acc else None
                except Exception:
                    account_type = None
            async with self._lock:
                runtime = self._runtimes[execution_id]
        else:
            async with async_session() as db:
                try:
                    from account_trading.repository import AccountTradingRepository

                    repo = AccountTradingRepository(db)
                    acc = await repo.get_account(runtime.account_id)
                    account_type = acc.account_type if acc else None
                except Exception:
                    pass

        if allow_same_bar is None:
            allow_same_bar = account_type != "live"

        tick_lock = self._tick_lock_for(execution_id)
        async with tick_lock:
            if allow_same_bar:
                runtime.last_processed_bar = None
            elif (
                runtime.last_processed_bar
                and runtime.last_ordered_bar
                and runtime.last_ordered_bar == runtime.last_processed_bar
            ):
                return {
                    "skipped": True,
                    "reason": "live_same_bar_blocked",
                    "bar_time": runtime.last_processed_bar,
                    "message": "实盘禁止同一根 K 线重复下单",
                }
            return await self._process_one(runtime)

    async def _loop(self) -> None:
        try:
            await self._restore_running()
        except Exception:
            logger.exception("restore running executions failed")

        while not self._stop_event.is_set():
            try:
                await self._tick_all()
            except Exception:
                logger.exception("execution worker tick_all failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=DEFAULT_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass

    async def _restore_running(self) -> None:
        from sqlalchemy import select

        async with async_session() as db:
            result = await db.execute(select(Execution).where(Execution.status == "running"))
            rows = result.scalars().all()
            for execution in rows:
                await self.register(execution)
            logger.info("restored %s running executions", len(rows))

    async def _tick_all(self) -> None:
        async with self._lock:
            runtimes = list(self._runtimes.values())
        for runtime in runtimes:
            tick_lock = self._tick_lock_for(runtime.execution_id)
            if tick_lock.locked():
                continue
            try:
                async with tick_lock:
                    await self._process_one(runtime)
            except Exception:
                logger.exception("process execution %s failed", runtime.execution_id)

    async def _process_one(self, runtime: ExecutionRuntime) -> dict:
        async with async_session() as db:
            execution = await db.get(Execution, runtime.execution_id)
            if not execution:
                await self.unregister(runtime.execution_id)
                return {"skipped": True, "reason": "execution_missing"}
            if execution.status != "running":
                if execution.status == "stopped":
                    await self.unregister(runtime.execution_id)
                return {"skipped": True, "reason": f"status={execution.status}"}

            try:
                tick = await run_tick(db, runtime)
            except Exception as exc:
                from strategy_engine.exceptions import (
                    InvalidStrategyError,
                    StrategyNotActive,
                    StrategyNotFound,
                )

                if isinstance(exc, (StrategyNotActive, StrategyNotFound, InvalidStrategyError)):
                    logger.warning(
                        "execution %s strategy unavailable (%s), unregister",
                        runtime.execution_id,
                        exc,
                    )
                    try:
                        execution.status = "error"
                        execution.remark = str(exc)[:240]
                        await db.commit()
                    except Exception:
                        await db.rollback()
                    await self.unregister(runtime.execution_id)
                    return {"skipped": True, "reason": str(exc)}
                raise
            summary: dict = {
                "execution_id": runtime.execution_id,
                "bar_time": tick.bar_time,
                "price": tick.price,
                "skipped": tick.skipped,
                "skip_reason": tick.skip_reason,
                "intents": len(tick.intents),
                "orders": [],
            }

            for line in tick.logs:
                db.add(
                    ExecutionLog(
                        execution_id=execution.id,
                        level="error" if line.startswith("[ERROR]") else "info",
                        category="strategy",
                        message=line[:500],
                    )
                )

            if tick.skipped:
                if tick.skip_reason and "异常" in (tick.skip_reason or ""):
                    db.add(
                        ExecutionLog(
                            execution_id=execution.id,
                            level="error",
                            category="execution",
                            message=tick.skip_reason,
                        )
                    )
                await db.commit()
                return summary

            from account_trading.repository import AccountTradingRepository
            from common.config import get_settings

            repo = AccountTradingRepository(db)
            account = await repo.get_account(runtime.account_id)
            if account is None:
                db.add(
                    ExecutionLog(
                        execution_id=execution.id,
                        level="error",
                        category="execution",
                        message=f"账户不存在: {runtime.account_id}",
                    )
                )
                await db.commit()
                summary["skip_reason"] = "account_missing"
                return summary

            live_same_bar_blocked = (
                account.account_type == "live"
                and bool(tick.bar_time)
                and runtime.last_ordered_bar == tick.bar_time
            )
            if live_same_bar_blocked and tick.intents:
                db.add(
                    ExecutionLog(
                        execution_id=execution.id,
                        level="warning",
                        category="order",
                        message=f"实盘同 bar({tick.bar_time}) 已下单，跳过 {len(tick.intents)} 笔委托",
                    )
                )
                for intent in tick.intents:
                    execution.total_signals = int(execution.total_signals or 0) + 1
                    db.add(
                        ExecutionSignal(
                            execution_id=execution.id,
                            strategy_id=str(runtime.strategy_id),
                            symbol=intent.security,
                            direction=intent.side,
                            signal_price=intent.price,
                            quantity=intent.quantity,
                            order_type="limit",
                            reason=intent.reason or intent.signal_label,
                            risk_passed=False,
                            risk_reason=f"live_same_bar_blocked:{tick.bar_time}",
                            order_status="skipped",
                        )
                    )
                    summary["orders"].append(
                        {
                            "symbol": intent.security,
                            "status": "live_same_bar_blocked",
                            "bar_time": tick.bar_time,
                        }
                    )
                await db.commit()
                summary["skip_reason"] = "live_same_bar_blocked"
                return summary

            settings = get_settings()
            live_async = account.account_type == "live" and bool(settings.LIVE_ORDER_ASYNC)
            placed_on_bar = False

            for intent in tick.intents:
                risk_ok, risk_reason = await self._check_risk(
                    db, execution.id, intent.security, intent.side, intent.quantity, intent.price
                )
                signal = ExecutionSignal(
                    execution_id=execution.id,
                    strategy_id=str(runtime.strategy_id),
                    symbol=intent.security,
                    direction=intent.side,
                    signal_price=intent.price,
                    quantity=intent.quantity,
                    order_type="limit",
                    reason=intent.reason or intent.signal_label,
                    risk_passed=risk_ok,
                    risk_reason=risk_reason,
                )
                execution.total_signals = int(execution.total_signals or 0) + 1

                if not risk_ok:
                    db.add(signal)
                    db.add(
                        RiskAlert(
                            execution_id=execution.id,
                            rule_type="execution_risk",
                            severity="warning",
                            title="风控拦截",
                            message=risk_reason or "风控未通过",
                        )
                    )
                    db.add(
                        ExecutionLog(
                            execution_id=execution.id,
                            level="warning",
                            category="risk",
                            message=f"风控拒绝 {intent.side} {intent.security} x{intent.quantity}: {risk_reason}",
                        )
                    )
                    summary["orders"].append({"symbol": intent.security, "status": "risk_rejected"})
                    continue

                if live_async:
                    signal.order_status = "submitting"
                    db.add(signal)
                    await db.flush()
                    signal_id = signal.id
                    execution.total_orders = int(execution.total_orders or 0) + 1
                    db.add(
                        ExecutionLog(
                            execution_id=execution.id,
                            level="info",
                            category="order",
                            message=(
                                f"实盘委托已提交后台: {intent.side} {intent.security} x{intent.quantity} "
                                f"@ {intent.price} (bar={tick.bar_time})"
                            ),
                        )
                    )
                    summary["orders"].append(
                        {
                            "symbol": intent.security,
                            "side": intent.side,
                            "quantity": intent.quantity,
                            "status": "submitting",
                            "engine": "desktop_live",
                            "async": True,
                        }
                    )
                    placed_on_bar = True
                    asyncio.create_task(
                        self._place_live_order_background(
                            execution_id=execution.id,
                            signal_id=signal_id,
                            account_id=account.id,
                            side=intent.side,
                            symbol=intent.security,
                            price=intent.price,
                            quantity=intent.quantity,
                            bar_time=tick.bar_time,
                        ),
                        name=f"live-order-{execution.id}-{signal_id}",
                    )
                    continue

                try:
                    result = await place_execution_order(
                        db,
                        account,
                        side=intent.side,
                        symbol=intent.security,
                        price=intent.price,
                        quantity=intent.quantity,
                        remark=f"execution:{execution.id} bar:{tick.bar_time}",
                    )
                    signal.order_id = result.get("order_id")
                    signal.order_status = result.get("order_status")
                    signal.filled_price = result.get("filled_price")
                    signal.filled_quantity = result.get("filled_quantity")
                    if result.get("captcha_required"):
                        signal.risk_reason = "captcha_required"
                    else:
                        execution.total_orders = int(execution.total_orders or 0) + 1
                        placed_on_bar = True
                    db.add(signal)
                    db.add(
                        ExecutionLog(
                            execution_id=execution.id,
                            level="warning" if result.get("captcha_required") else "info",
                            category="order",
                            message=(
                                f"{'验证码未完成' if result.get('captcha_required') else '已下单'} "
                                f"{intent.side} {intent.security} x{intent.quantity} "
                                f"@ {intent.price} → {signal.order_id} ({account.account_type})"
                            ),
                            details=result,
                        )
                    )
                    summary["orders"].append(
                        {
                            "symbol": intent.security,
                            "side": intent.side,
                            "quantity": intent.quantity,
                            "order_id": signal.order_id,
                            "status": signal.order_status,
                            "engine": result.get("engine"),
                            "captcha_required": bool(result.get("captcha_required")),
                        }
                    )
                except Exception as exc:
                    signal.risk_passed = False
                    signal.risk_reason = str(exc)
                    signal.order_status = "failed"
                    db.add(signal)
                    db.add(
                        ExecutionLog(
                            execution_id=execution.id,
                            level="error",
                            category="order",
                            message=f"下单失败 {intent.side} {intent.security}: {exc}",
                        )
                    )
                    summary["orders"].append(
                        {"symbol": intent.security, "status": "failed", "error": str(exc)}
                    )

            if placed_on_bar and tick.bar_time:
                runtime.last_ordered_bar = tick.bar_time

            if not tick.intents:
                db.add(
                    ExecutionLog(
                        execution_id=execution.id,
                        level="info",
                        category="execution",
                        message=f"tick {tick.bar_time} 无交易信号 @ {tick.price}",
                    )
                )

            await db.commit()
            return summary

    async def _place_live_order_background(
        self,
        *,
        execution_id: int,
        signal_id: int,
        account_id: int,
        side: str,
        symbol: str,
        price: float,
        quantity: int,
        bar_time: str,
    ) -> None:
        """实盘下单后台任务：不占用其它策略的 tick 循环。"""
        try:
            async with async_session() as db:
                from account_trading.repository import AccountTradingRepository

                repo = AccountTradingRepository(db)
                account = await repo.get_account(account_id)
                signal = await db.get(ExecutionSignal, signal_id)
                if account is None or signal is None:
                    return
                try:
                    result = await place_execution_order(
                        db,
                        account,
                        side=side,
                        symbol=symbol,
                        price=price,
                        quantity=quantity,
                        remark=f"execution:{execution_id} bar:{bar_time} async",
                    )
                    signal.order_id = result.get("order_id")
                    signal.order_status = result.get("order_status") or (
                        "captcha_required" if result.get("captcha_required") else "submitted"
                    )
                    signal.filled_price = result.get("filled_price")
                    signal.filled_quantity = result.get("filled_quantity")
                    if result.get("captcha_required"):
                        signal.risk_reason = "captcha_required"
                    db.add(
                        ExecutionLog(
                            execution_id=execution_id,
                            level="warning" if result.get("captcha_required") else "info",
                            category="order",
                            message=(
                                f"后台实盘结果: {side} {symbol} → {signal.order_status} "
                                f"order_id={signal.order_id}"
                            ),
                            details=result,
                        )
                    )
                    async with self._lock:
                        rt = self._runtimes.get(execution_id)
                        if rt and bar_time:
                            rt.last_ordered_bar = bar_time
                    await db.commit()
                except Exception as exc:
                    signal.order_status = "failed"
                    signal.risk_reason = str(exc)
                    db.add(
                        ExecutionLog(
                            execution_id=execution_id,
                            level="error",
                            category="order",
                            message=f"后台实盘下单失败 {side} {symbol}: {exc}",
                        )
                    )
                    async with self._lock:
                        rt = self._runtimes.get(execution_id)
                        if rt and bar_time:
                            rt.last_ordered_bar = bar_time
                    await db.commit()
        except Exception:
            logger.exception(
                "background live order failed execution=%s signal=%s",
                execution_id,
                signal_id,
            )

    async def _check_risk(
        self,
        db,
        execution_id: int,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
    ) -> tuple[bool, Optional[str]]:
        from sqlalchemy import select

        result = await db.execute(select(RiskRule).where(RiskRule.enabled.is_(True)))
        rules = result.scalars().all()
        for rule in rules:
            params = rule.params or {}
            rule_type = rule.rule_type
            if rule_type in {"single_stock_limit", "max_position_size"}:
                raw = params.get("max_quantity")
                if raw is None:
                    raw = params.get("max_position_size")
                if raw is None:
                    thr = params.get("threshold")
                    if thr is not None and float(thr) >= 1:
                        raw = thr
                if raw is None:
                    continue
                max_qty = int(float(raw))
                if max_qty > 1 and quantity > max_qty:
                    return False, f"单品种数量超限: {quantity} > {max_qty}"
            if rule_type in {"daily_loss_limit", "max_daily_loss", "position_limit", "max_drawdown"}:
                continue
        return True, None


_worker: Optional[ExecutionWorker] = None


def get_execution_worker() -> ExecutionWorker:
    global _worker
    if _worker is None:
        _worker = ExecutionWorker()
    return _worker
