from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .adapters.ths_desktop import CaptchaRequiredError, ThsDesktopAdapter
from .ths_settings import light_verify, skip_context_status_seconds


OrderSide = Literal["buy", "sell"]
OrderScope = Literal["today", "history"]


DEFAULT_THS_CLIENT_PATH = r"E:\同花顺软件\同花顺\xiadan.exe"


@dataclass
class AutomationLog:
    id: str
    operation: str
    status: str
    message: str
    created_at: str
    detail: dict[str, Any] = field(default_factory=dict)


class AccountTradingService:
    """Application service for the account trading module.

    The desktop trading client is a single local UI resource, so all adapter
    calls are serialized through one process lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._logs: list[AutomationLog] = []
        self._status_cache_lock = threading.Lock()
        self._status_cache: dict[str, Any] | None = None
        self._status_cache_at: float = 0.0
        self._status_cache_path: str | None = None

    def status(self, client_path: str | None = None, *, probe: bool = True) -> dict[str, Any]:
        """probe=True：不自动启动客户端，短超时失败快返回（供状态查询）。"""
        result = self._run(
            "status",
            client_path,
            lambda adapter: adapter.status(),
            probe=probe,
        )
        self._remember_status(client_path, result)
        return result

    def get_cached_status(self, client_path: str | None = None) -> dict[str, Any] | None:
        ttl = skip_context_status_seconds()
        if ttl <= 0:
            return None
        path_key = str(self._resolve_client_path(client_path))
        with self._status_cache_lock:
            if not self._status_cache:
                return None
            if self._status_cache_path != path_key:
                return None
            if (time.monotonic() - self._status_cache_at) > ttl:
                return None
            return dict(self._status_cache)

    def invalidate_status_cache(self) -> None:
        with self._status_cache_lock:
            self._status_cache = None
            self._status_cache_at = 0.0
            self._status_cache_path = None

    def _remember_status(self, client_path: str | None, data: dict[str, Any]) -> None:
        if not isinstance(data, dict) or not data.get("connected"):
            return
        path_key = str(self._resolve_client_path(client_path))
        with self._status_cache_lock:
            self._status_cache = dict(data)
            self._status_cache_at = time.monotonic()
            self._status_cache_path = path_key

    def prepare_trading_workspace(self, client_path: str | None = None) -> dict[str, Any]:
        result = self._run(
            "prepare_trading_workspace",
            client_path,
            lambda adapter: adapter.prepare_trading_workspace(),
            probe=False,
        )
        self._remember_status(client_path, result)
        return result

    def balance(
        self,
        client_path: str | None = None,
        wait_manual_captcha: bool = False,
        manual_captcha_timeout: int = 120,
    ) -> dict[str, Any]:
        return self._run(
            "balance",
            client_path,
            lambda adapter: self._normalize_balance(adapter.get_balance()),
            wait_manual_captcha=wait_manual_captcha,
            manual_captcha_timeout=manual_captcha_timeout,
        )

    def positions(
        self,
        client_path: str | None = None,
        wait_manual_captcha: bool = False,
        manual_captcha_timeout: int = 120,
    ) -> list[dict[str, Any]]:
        return self._run(
            "positions",
            client_path,
            lambda adapter: [self._normalize_position(row) for row in adapter.get_positions()],
            wait_manual_captcha=wait_manual_captcha,
            manual_captcha_timeout=manual_captcha_timeout,
        )

    def orders(
        self,
        scope: OrderScope = "today",
        client_path: str | None = None,
        wait_manual_captcha: bool = False,
        manual_captcha_timeout: int = 120,
    ) -> list[dict[str, Any]]:
        def work(adapter: ThsDesktopAdapter) -> list[dict[str, Any]]:
            rows = adapter.get_today_orders() if scope == "today" else adapter.get_history_orders()
            return [
                order
                for row in rows
                if self._row_has_value(row)
                for order in [self._normalize_order(row)]
                if order["broker_order_id"] or order["symbol"] or order["quantity"]
            ]

        return self._run(
            f"{scope}_orders",
            client_path,
            work,
            wait_manual_captcha=wait_manual_captcha,
            manual_captcha_timeout=manual_captcha_timeout,
        )

    def trades(
        self,
        scope: OrderScope = "today",
        client_path: str | None = None,
        wait_manual_captcha: bool = False,
        manual_captcha_timeout: int = 120,
    ) -> list[dict[str, Any]]:
        def work(adapter: ThsDesktopAdapter) -> list[dict[str, Any]]:
            rows = adapter.get_today_trades() if scope == "today" else adapter.get_history_trades()
            return [
                trade
                for row in rows
                if self._row_has_value(row)
                for trade in [self._normalize_trade(row)]
                if trade["broker_trade_id"] or trade["symbol"] or trade["quantity"]
            ]

        return self._run(
            f"{scope}_trades",
            client_path,
            work,
            wait_manual_captcha=wait_manual_captcha,
            manual_captcha_timeout=manual_captcha_timeout,
        )

    def place_order(
        self,
        *,
        side: OrderSide,
        symbol: str,
        price: Decimal,
        quantity: int,
        client_path: str | None = None,
        wait_manual_captcha: bool = True,
        manual_captcha_timeout: int = 180,
        idempotency_key: str | None = None,
        remark: str | None = None,
    ) -> dict[str, Any]:
        operation = f"{side}_order"

        def work(adapter: ThsDesktopAdapter) -> dict[str, Any]:
            result = adapter.buy(symbol, price, quantity) if side == "buy" else adapter.sell(symbol, price, quantity)
            result["request"] = {
                "symbol": symbol,
                "side": side,
                "price": str(price),
                "quantity": quantity,
                "idempotency_key": idempotency_key,
                "remark": remark,
            }
            return result

        return self._run(
            operation,
            client_path,
            work,
            wait_manual_captcha=wait_manual_captcha,
            manual_captcha_timeout=manual_captcha_timeout,
        )

    def cancel_order(
        self,
        entrust_no: str,
        client_path: str | None = None,
        wait_manual_captcha: bool = True,
        manual_captcha_timeout: int = 180,
    ) -> dict[str, Any]:
        return self._run(
            "cancel_order",
            client_path,
            lambda adapter: adapter.cancel_order(entrust_no),
            wait_manual_captcha=wait_manual_captcha,
            manual_captcha_timeout=manual_captcha_timeout,
        )

    def cancel_all_orders(
        self,
        client_path: str | None = None,
        wait_manual_captcha: bool = True,
        manual_captcha_timeout: int = 180,
    ) -> dict[str, Any]:
        return self._run(
            "cancel_all_orders",
            client_path,
            lambda adapter: adapter.cancel_all_orders(),
            wait_manual_captcha=wait_manual_captcha,
            manual_captcha_timeout=manual_captcha_timeout,
        )

    def confirm_order(
        self,
        *,
        entrust_no: str | None = None,
        symbol: str | None = None,
        side: OrderSide | None = None,
        price: Decimal | str | None = None,
        quantity: int | None = None,
        client_path: str | None = None,
        wait_manual_captcha: bool = True,
        manual_captcha_timeout: int = 180,
    ) -> dict[str, Any]:
        use_light = light_verify()

        def work(adapter: ThsDesktopAdapter) -> dict[str, Any]:
            errors: list[str] = []
            orders: list[dict[str, Any]] = []
            trades: list[dict[str, Any]] = []
            balance: dict[str, Any] = {}

            try:
                orders = [
                    order
                    for row in adapter.get_today_orders()
                    if self._row_has_value(row)
                    for order in [self._normalize_order(row)]
                    if order["broker_order_id"] or order["symbol"] or order["quantity"]
                ]
            except Exception as exc:
                errors.append(f"orders: {type(exc).__name__}: {exc}")
            if not use_light:
                try:
                    trades = [
                        trade
                        for row in adapter.get_today_trades()
                        if self._row_has_value(row)
                        for trade in [self._normalize_trade(row)]
                        if trade["broker_trade_id"] or trade["symbol"] or trade["quantity"]
                    ]
                except Exception as exc:
                    errors.append(f"trades: {type(exc).__name__}: {exc}")
                try:
                    balance = self._normalize_balance(adapter.get_balance())
                except Exception as exc:
                    errors.append(f"balance: {type(exc).__name__}: {exc}")

            matched_orders = self._match_normalized_orders(orders, entrust_no, symbol, side, price, quantity)
            matched_trades = self._match_normalized_trades(trades, entrust_no, symbol, side, price, quantity)
            confirmed = bool(matched_orders or matched_trades)
            final_status = self._confirmation_status(matched_orders, matched_trades, confirmed, errors)
            return {
                "confirmed": confirmed,
                "final_status": final_status,
                "entrust_no": entrust_no,
                "matched_orders": matched_orders,
                "matched_trades": matched_trades,
                "orders": orders,
                "trades": trades,
                "balance": balance,
                "errors": errors,
                "light_verify": use_light,
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }

        return self._run(
            "confirm_order",
            client_path,
            work,
            wait_manual_captcha=wait_manual_captcha,
            manual_captcha_timeout=manual_captcha_timeout,
        )

    def sync(
        self,
        client_path: str | None = None,
        wait_manual_captcha: bool = True,
        manual_captcha_timeout: int = 180,
    ) -> dict[str, Any]:
        def work(adapter: ThsDesktopAdapter) -> dict[str, Any]:
            return {
                "balance": self._normalize_balance(adapter.get_balance()),
                "positions": [self._normalize_position(row) for row in adapter.get_positions()],
                "orders": [
                    order
                    for row in adapter.get_today_orders()
                    if self._row_has_value(row)
                    for order in [self._normalize_order(row)]
                    if order["broker_order_id"] or order["symbol"] or order["quantity"]
                ],
                "trades": [
                    trade
                    for row in adapter.get_today_trades()
                    if self._row_has_value(row)
                    for trade in [self._normalize_trade(row)]
                    if trade["broker_trade_id"] or trade["symbol"] or trade["quantity"]
                ],
            }

        return self._run(
            "sync",
            client_path,
            work,
            wait_manual_captcha=wait_manual_captcha,
            manual_captcha_timeout=manual_captcha_timeout,
        )

    def logs(self, limit: int = 50) -> list[dict[str, Any]]:
        return [log.__dict__ for log in self._logs[-limit:]][::-1]

    def _run(
        self,
        operation: str,
        client_path: str | None,
        work: Any,
        *,
        wait_manual_captcha: bool = False,
        manual_captcha_timeout: int = 120,
        probe: bool = False,
    ) -> Any:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("同花顺自动化正在执行其他任务，请稍后再试。")

        try:
            adapter = ThsDesktopAdapter(
                self._resolve_client_path(client_path),
                wait_manual_captcha=wait_manual_captcha,
                manual_captcha_timeout=manual_captcha_timeout,
            )
            if probe:
                adapter.connect_probe()
            else:
                adapter.connect()
            result = work(adapter)
            self._append_log(operation, "success", "操作完成", {"result": result})
            return result
        except CaptchaRequiredError as exc:
            detail = {"captcha_required": True}
            self._append_log(operation, "captcha_required", str(exc), detail)
            raise
        except Exception as exc:
            if operation == "status":
                self.invalidate_status_cache()
            self._append_log(operation, "failed", str(exc), {"error_type": type(exc).__name__})
            raise
        finally:
            self._lock.release()

    def _resolve_client_path(self, client_path: str | None) -> Path:
        path = client_path or os.getenv("THS_CLIENT_PATH") or DEFAULT_THS_CLIENT_PATH
        return Path(path)

    def _append_log(
        self,
        operation: str,
        status: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self._logs.append(
            AutomationLog(
                id=uuid4().hex,
                operation=operation,
                status=status,
                message=message,
                created_at=datetime.now().isoformat(timespec="seconds"),
                detail=detail or {},
            )
        )
        if len(self._logs) > 200:
            self._logs = self._logs[-200:]

    def _normalize_balance(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "account_id": "ths-local",
            "mode": "live",
            "total_asset": self._decimal_text(row.get("总资产")),
            "available_cash": self._decimal_text(row.get("可用金额")),
            "withdrawable_cash": self._decimal_text(row.get("可取金额")),
            "market_value": self._decimal_text(row.get("股票市值")),
            "cash_balance": self._decimal_text(row.get("资金余额")),
            "frozen_cash": self._decimal_text(
                Decimal(str(row.get("资金余额", 0) or 0)) - Decimal(str(row.get("可用金额", 0) or 0))
            ),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "raw": row,
        }

    def _normalize_position(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "position_id": self._first(row, "证券代码", "position_id"),
            "symbol": self._first(row, "证券代码", "代码"),
            "name": self._first(row, "证券名称", "名称"),
            "quantity": self._int(row, "股票余额", "持仓数量", "当前持仓"),
            "available_quantity": self._int(row, "可用余额", "可卖数量"),
            "cost_price": self._decimal_text(self._first(row, "成本价", "成本价格")),
            "last_price": self._decimal_text(self._first(row, "市价", "最新价")),
            "market_value": self._decimal_text(self._first(row, "市值", "股票市值")),
            "unrealized_pnl": self._decimal_text(self._first(row, "浮动盈亏", "盈亏")),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "raw": row,
        }

    def _normalize_order(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "order_id": self._first(row, "合同编号", "委托编号", "申请编号"),
            "broker_order_id": self._first(row, "合同编号", "委托编号", "申请编号"),
            "symbol": self._first(row, "证券代码", "代码"),
            "name": self._first(row, "证券名称", "名称"),
            "side": self._normalize_side(self._first(row, "买卖标志", "操作", "委托类别")),
            "price": self._decimal_text(self._first(row, "委托价格", "价格")),
            "quantity": self._int(row, "委托数量", "数量"),
            "filled_quantity": self._int(row, "成交数量", "已成交数量"),
            "canceled_quantity": self._int(row, "撤单数量", "已撤数量", "撤单"),
            "avg_fill_price": self._decimal_text(self._first(row, "成交均价", "成交价格")),
            "status": self._first(row, "委托状态", "状态", "状态说明", "委托状态说明", "处理结果", "备注", "说明"),
            "submitted_at": self._first(row, "委托时间", "申报时间", "操作日期"),
            "raw": row,
        }

    def _normalize_trade(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "trade_id": self._first(row, "成交编号", "合同编号", "委托编号"),
            "broker_trade_id": self._first(row, "成交编号", "合同编号"),
            "order_id": self._first(row, "合同编号", "委托编号"),
            "symbol": self._first(row, "证券代码", "代码"),
            "name": self._first(row, "证券名称", "名称"),
            "side": self._normalize_side(self._first(row, "买卖标志", "操作", "委托类别")),
            "price": self._decimal_text(self._first(row, "成交价格", "成交均价", "价格")),
            "quantity": self._int(row, "成交数量", "数量"),
            "amount": self._decimal_text(self._first(row, "成交金额", "发生金额")),
            "commission": self._decimal_text(self._first(row, "佣金")),
            "stamp_tax": self._decimal_text(self._first(row, "印花税")),
            "traded_at": self._first(row, "成交时间", "发生时间", "操作日期"),
            "source": "live",
            "raw": row,
        }

    def _match_normalized_orders(
        self,
        rows: list[dict[str, Any]],
        entrust_no: str | None,
        symbol: str | None,
        side: str | None,
        price: Decimal | str | None,
        quantity: int | None,
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in rows
            if self._is_order_match(row, entrust_no, symbol, side, price, quantity)
        ]

    def _match_normalized_trades(
        self,
        rows: list[dict[str, Any]],
        entrust_no: str | None,
        symbol: str | None,
        side: str | None,
        price: Decimal | str | None,
        quantity: int | None,
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in rows
            if self._is_trade_match(row, entrust_no, symbol, side, price, quantity)
        ]

    def _is_order_match(
        self,
        row: dict[str, Any],
        entrust_no: str | None,
        symbol: str | None,
        side: str | None,
        price: Decimal | str | None,
        quantity: int | None,
    ) -> bool:
        if entrust_no and str(row.get("broker_order_id") or row.get("order_id") or "").strip() == str(entrust_no).strip():
            return True
        return self._is_symbol_side_qty_price_match(row, symbol, side, price, quantity)

    def _is_trade_match(
        self,
        row: dict[str, Any],
        entrust_no: str | None,
        symbol: str | None,
        side: str | None,
        price: Decimal | str | None,
        quantity: int | None,
    ) -> bool:
        if entrust_no and str(row.get("order_id") or "").strip() == str(entrust_no).strip():
            return True
        return self._is_symbol_side_qty_price_match(row, symbol, side, price, quantity, allow_partial_quantity=True)

    def _is_symbol_side_qty_price_match(
        self,
        row: dict[str, Any],
        symbol: str | None,
        side: str | None,
        price: Decimal | str | None,
        quantity: int | None,
        *,
        allow_partial_quantity: bool = False,
    ) -> bool:
        if symbol and str(row.get("symbol") or "").strip() != str(symbol).strip():
            return False
        if side and str(row.get("side") or "").strip() != str(side).strip():
            return False
        if quantity:
            row_quantity = self._int(row, "quantity")
            if allow_partial_quantity:
                if row_quantity <= 0 or row_quantity > quantity:
                    return False
            elif row_quantity != quantity:
                return False
        if price is not None:
            expected = self._decimal_text(price)
            actual = self._decimal_text(row.get("price"))
            if actual != expected:
                return False
        return bool(symbol or side or quantity or price is not None)

    def _confirmation_status(
        self,
        matched_orders: list[dict[str, Any]],
        matched_trades: list[dict[str, Any]],
        confirmed: bool,
        errors: list[str],
    ) -> str:
        if matched_trades:
            order = matched_orders[0] if matched_orders else {}
            filled_quantity = self._int(order, "filled_quantity")
            quantity = self._int(order, "quantity")
            if quantity and filled_quantity and filled_quantity < quantity:
                return "partial_filled"
            if quantity and filled_quantity >= quantity:
                return "filled"
            return "filled"
        if matched_orders:
            return str(matched_orders[0].get("status") or "submitted")
        if errors:
            return "confirmation_failed"
        return "unconfirmed"

    def _first(self, row: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return value
        return ""

    def _int(self, row: dict[str, Any], *keys: str) -> int:
        value = self._first(row, *keys)
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return 0

    def _decimal_text(self, value: Any) -> str:
        try:
            return f"{Decimal(str(value or 0)):.2f}"
        except Exception:
            return "0.00"

    def _normalize_side(self, value: Any) -> str:
        text = str(value or "")
        if "买" in text:
            return "buy"
        if "卖" in text:
            return "sell"
        return text

    def _row_has_value(self, row: dict[str, Any]) -> bool:
        return any(str(value).strip() not in ("", "0", "0.0", "0.00") for value in row.values())


account_trading_service = AccountTradingService()
