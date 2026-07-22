"""订单桥接：策略执行 → account_trading（按账户类型自动分流）。

paper → PaperTradingEngine
live  → 同花顺桌面适配器（默认不等待人工验证码，失败快返回；可后台异步提交）
backtest → 拒绝（应走回测入口）
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from account_trading.adapters.ths_desktop import CaptchaRequiredError
from account_trading.models import TradingAccount
from account_trading.repository import AccountTradingRepository
from account_trading.service import account_trading_service
from common.config import get_settings

logger = logging.getLogger(__name__)


async def place_execution_order(
    db: AsyncSession,
    account: TradingAccount,
    *,
    side: str,
    symbol: str,
    price: float,
    quantity: int,
    name: str | None = None,
    remark: str | None = None,
    source: str = "strategy_execution",
) -> dict[str, Any]:
    """按账户类型路由下单，返回统一结构。"""
    repo = AccountTradingRepository(db)
    price_dec = Decimal(str(round(float(price), 4)))
    qty = int(quantity)
    if qty <= 0:
        raise ValueError("委托数量必须大于 0")
    if side not in {"buy", "sell"}:
        raise ValueError("委托方向必须是 buy 或 sell")

    # 实盘委托代码用 6 位；paper/腾讯行情也兼容带后缀
    order_symbol = symbol.split(".")[0] if "." in symbol else symbol
    idempotency_key = f"exec-{source}-{uuid4().hex[:16]}"

    if account.account_type == "paper":
        task = await repo.create_task(account, side, {
            "source": source,
            "symbol": symbol,
            "price": float(price_dec),
            "quantity": qty,
        })
        try:
            data = await repo.place_paper_order(
                account,
                side=side,
                symbol=order_symbol,
                name=name,
                price=price_dec,
                quantity=qty,
                idempotency_key=idempotency_key,
                remark=remark or f"策略执行自动下单 ({source})",
            )
            await repo.finish_task(task, "success", data)
            return _normalize_result(account, data, engine="paper_trading")
        except Exception as exc:
            await repo.finish_task(task, "failed", {"error": str(exc)})
            raise

    if account.account_type == "backtest":
        raise ValueError("回测账户不支持实盘/模拟执行下单，请使用历史回放回测入口")

    if account.account_type != "live":
        raise ValueError(f"不支持的账户类型: {account.account_type}")

    settings = get_settings()
    wait_captcha = bool(settings.LIVE_WAIT_MANUAL_CAPTCHA)
    captcha_timeout = int(settings.LIVE_MANUAL_CAPTCHA_TIMEOUT or 30)

    client_path = await repo.get_active_client_path(account)
    task = await repo.create_task(account, side, {
        "source": source,
        "symbol": symbol,
        "price": float(price_dec),
        "quantity": qty,
        "idempotency_key": idempotency_key,
        "wait_manual_captcha": wait_captcha,
    })
    try:
        data = await run_in_threadpool(
            account_trading_service.place_order,
            side=side,
            symbol=order_symbol,
            price=float(price_dec),
            quantity=qty,
            client_path=client_path,
            wait_manual_captcha=wait_captcha,
            manual_captcha_timeout=captcha_timeout,
            idempotency_key=idempotency_key,
            remark=remark or f"策略执行自动下单 ({source})",
        )
        await repo.save_place_order_result(account, data)
        await repo.save_trades(account, data.get("matched_trades") or [])
        await repo.save_balance(account, data.get("balance_after") or {})
        await repo.finish_task(task, "success", data)
        return _normalize_result(account, data, engine="desktop_live")
    except CaptchaRequiredError as exc:
        payload = {
            "status": "captcha_required",
            "captcha_required": True,
            "message": str(exc) or "需要人工完成验证码，订单未完成",
            "idempotency_key": idempotency_key,
        }
        await repo.finish_task(task, "captcha_required", payload)
        return _normalize_result(account, payload, engine="desktop_live")
    except Exception as exc:
        await repo.finish_task(task, "failed", {"error": str(exc)})
        raise


def _normalize_result(account: TradingAccount, data: dict[str, Any], *, engine: str) -> dict[str, Any]:
    order = data.get("order") or {}
    trade = data.get("trade") or {}
    order_id = (
        str(order.get("order_no") or order.get("broker_order_no") or order.get("id") or "")
        or str(data.get("order_id") or data.get("entrust_no") or "")
    )
    status = str(
        order.get("status")
        or data.get("status")
        or ("filled" if trade else "submitted")
    )
    filled_price = None
    filled_qty = None
    if trade:
        filled_price = float(trade.get("price") or trade.get("fill_price") or 0) or None
        filled_qty = int(trade.get("quantity") or trade.get("fill_quantity") or 0) or None
    if filled_price is None and data.get("matched_trades"):
        first = data["matched_trades"][0]
        filled_price = float(first.get("price") or 0) or None
        filled_qty = int(first.get("quantity") or 0) or None

    return {
        "account_id": account.id,
        "account_type": account.account_type,
        "engine": engine,
        "order_id": order_id or None,
        "order_status": status,
        "filled_price": filled_price,
        "filled_quantity": filled_qty,
        "captcha_required": bool(data.get("captcha_required")),
        "raw": data,
    }
