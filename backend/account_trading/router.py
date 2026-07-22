from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from common.dependencies import get_db
from common.utils.response import ApiResponse

from .adapters.ths_desktop import CaptchaRequiredError, TradingClientNotReadyError
from .repository import AccountTradingRepository, _normalize_order_status
from .service import account_trading_service
from .ths_settings import skip_post_order_confirm


router = APIRouter(prefix="/api/account", tags=["账户与交易"])


OrderSide = Literal["buy", "sell"]
AccountMode = Literal["live", "paper", "backtest"]
OrderScope = Literal["today", "history"]
ManagedAccountType = Literal["live", "paper", "backtest"]
ManagedAccountStatus = Literal["active", "inactive", "archived"]
BindingType = Literal["desktop", "ths", "webapi", "mock", "backtest"]


class AccountManageRequest(BaseModel):
    account_code: str | None = Field(default=None, max_length=64)
    account_name: str = Field(..., min_length=1, max_length=128)
    account_type: ManagedAccountType
    broker_name: str | None = Field(default=None, max_length=64)
    broker_account_no: str | None = Field(default=None, max_length=64)
    shareholder_account: str | None = Field(default=None, max_length=64)
    exchange: str | None = Field(default=None, max_length=16)
    status: ManagedAccountStatus = "active"
    is_default: bool = False
    binding_type: BindingType | None = None
    client_path: str | None = Field(default=None, max_length=255)
    client_identity: str | None = Field(default=None, max_length=128)
    binding_active: bool = True
    initial_cash: Decimal | None = Field(default=None, gt=0, description="模拟/回测账户初始资金")
    meta_json: dict[str, Any] | None = None


class AccountUpdateRequest(BaseModel):
    account_code: str | None = Field(default=None, max_length=64)
    account_name: str | None = Field(default=None, min_length=1, max_length=128)
    account_type: ManagedAccountType | None = None
    broker_name: str | None = Field(default=None, max_length=64)
    broker_account_no: str | None = Field(default=None, max_length=64)
    shareholder_account: str | None = Field(default=None, max_length=64)
    exchange: str | None = Field(default=None, max_length=16)
    status: ManagedAccountStatus | None = None
    is_default: bool | None = None
    binding_type: BindingType | None = None
    client_path: str | None = Field(default=None, max_length=255)
    client_identity: str | None = Field(default=None, max_length=128)
    binding_active: bool | None = None
    initial_cash: Decimal | None = Field(default=None, gt=0, description="模拟/回测账户初始资金")
    meta_json: dict[str, Any] | None = None


class OrderRequest(BaseModel):
    symbol: str = Field(..., min_length=6, max_length=12, description="证券代码")
    name: str | None = Field(default=None, max_length=128, description="证券名称")
    side: OrderSide
    price: Decimal = Field(..., gt=0, description="限价委托价格")
    quantity: int = Field(..., gt=0, description="委托股数")
    mode: AccountMode = "live"
    account_id: int | None = Field(default=None, gt=0, description="前端选择的交易账户ID")
    idempotency_key: str | None = Field(default=None, max_length=80)
    remark: str | None = Field(default=None, max_length=200)
    wait_manual_captcha: bool = True
    manual_captcha_timeout: int = Field(default=180, ge=30, le=600)

    @field_validator("quantity")
    @classmethod
    def validate_a_share_lot(cls, value: int) -> int:
        if value % 100 != 0:
            raise ValueError("A 股委托数量必须是 100 股的整数倍。")
        return value

    @model_validator(mode="after")
    def validate_live_idempotency(self) -> "OrderRequest":
        if self.mode == "live" and not self.idempotency_key:
            raise ValueError("实盘下单必须提供 idempotency_key，避免重复下单。")
        return self


class CancelRequest(BaseModel):
    account_id: int | None = Field(default=None, gt=0, description="前端选择的交易账户ID")
    wait_manual_captcha: bool = True
    manual_captcha_timeout: int = Field(default=180, ge=30, le=600)


class SyncRequest(BaseModel):
    account_id: int | None = Field(default=None, gt=0, description="前端选择的交易账户ID")
    wait_manual_captcha: bool = True
    manual_captcha_timeout: int = Field(default=180, ge=30, le=600)


class AutomationConnectRequest(BaseModel):
    account_id: int | None = Field(default=None, gt=0, description="前端选择的交易账户ID")


def ok(data: Any, message: str = "ok") -> ApiResponse:
    return ApiResponse(success=True, data=data, message=message)


def engine_label(account_type: str) -> str:
    if account_type == "live":
        return "desktop_live"
    if account_type == "paper":
        return "paper_trading"
    if account_type == "backtest":
        return "backtest_trading"
    return "unknown"


def raise_api_error(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, CaptchaRequiredError):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "检测到验证码弹窗，请在同花顺中人工处理后重试，或启用等待人工验证码。",
            },
        ) from exc
    if isinstance(exc, TradingClientNotReadyError):
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "type": "trading_client_not_ready",
            },
        ) from exc
    if isinstance(exc, RuntimeError) and "同花顺自动化正在执行其他任务" in str(exc):
        raise HTTPException(
            status_code=429,
            detail={
                "message": str(exc),
                "type": "trading_client_busy",
            },
        ) from exc
    raise HTTPException(status_code=500, detail={"message": str(exc), "type": type(exc).__name__}) from exc


def normalize_account_no(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isalnum())


def is_masked_account_no(value: Any) -> bool:
    return "*" in str(value or "")


def assert_desktop_account_matches(selected_account: Any, status_data: dict[str, Any]) -> None:
    status_account = status_data.get("account") or {}
    selected_capital_raw = getattr(selected_account, "broker_account_no", None)
    current_capital_raw = status_account.get("capital_account")
    selected_holder_raw = getattr(selected_account, "shareholder_account", None)
    current_holder_raw = status_account.get("shareholder_account")
    selected_capital = normalize_account_no(selected_capital_raw)
    current_capital = normalize_account_no(current_capital_raw)
    selected_holder = normalize_account_no(selected_holder_raw)
    current_holder = normalize_account_no(current_holder_raw)

    if (
        selected_capital
        and current_capital
        and not is_masked_account_no(selected_capital_raw)
        and not is_masked_account_no(current_capital_raw)
        and selected_capital != current_capital
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "当前选择的账户与同花顺已登录资金账号不一致，请切换账户或重新登录同花顺后再操作。",
                "type": "account_mismatch",
            },
        )
    if (
        selected_holder
        and current_holder
        and not is_masked_account_no(selected_holder_raw)
        and not is_masked_account_no(current_holder_raw)
        and selected_holder != current_holder
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "当前选择的账户与同花顺已登录股东账号不一致，请切换账户或重新登录同花顺后再操作。",
                "type": "account_mismatch",
            },
        )


async def get_account_context(
    db: AsyncSession,
    account_id: int | None = None,
    require_live: bool = False,
    *,
    force_status: bool = False,
) -> tuple[AccountTradingRepository, Any, dict[str, Any]]:
    repo = AccountTradingRepository(db)
    if account_id:
        account = await repo.get_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail={"message": "选择的账户不存在。", "type": "account_not_found"})
        if account.status == "archived":
            raise HTTPException(status_code=400, detail={"message": "选择的账户已归档，不能用于交易。", "type": "account_archived"})
        if require_live and account.account_type != "live":
            raise HTTPException(status_code=400, detail={"message": "当前桌面适配器只支持实盘账户交易。", "type": "unsupported_account_type"})
        status_data: dict[str, Any] = {}
        if account.account_type == "live":
            client_path = await repo.get_active_client_path(account)
            cached = None if force_status else account_trading_service.get_cached_status(client_path)
            if cached:
                status_data = cached
                status_data["status_cache_hit"] = True
            else:
                status_data = await run_in_threadpool(account_trading_service.status, client_path)
                status_data["status_cache_hit"] = False
            assert_desktop_account_matches(account, status_data)
    else:
        cached = None if force_status else account_trading_service.get_cached_status(None)
        if cached:
            status_data = cached
            status_data["status_cache_hit"] = True
        else:
            status_data = await run_in_threadpool(account_trading_service.status)
            status_data["status_cache_hit"] = False
        account = await repo.ensure_account(status_data.get("account") or {})
        if require_live and account.account_type != "live":
            raise HTTPException(status_code=400, detail={"message": "未选择账户时只能跟随同花顺实盘账户。", "type": "unsupported_account_type"})
    if account.account_type == "live":
        await repo.update_runtime_status(account, status_data)
    return repo, account, status_data


def with_selected_account_context(data: dict[str, Any], account: Any) -> dict[str, Any]:
    enriched = dict(data)
    enriched["selected_account"] = {
        "id": account.id,
        "account_code": account.account_code,
        "account_name": account.account_name,
        "account_type": account.account_type,
        "account_type_label": {
            "live": "实盘账户",
            "paper": "模拟账户",
            "backtest": "回测账户",
        }.get(account.account_type, "未知账户"),
    }
    enriched["selected_engine"] = engine_label(account.account_type)
    return enriched


async def get_managed_account(repo: AccountTradingRepository, account_id: int) -> Any:
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail={"message": "选择的账户不存在。", "type": "account_not_found"})
    if account.status == "archived":
        raise HTTPException(status_code=400, detail={"message": "选择的账户已归档，不能用于查询。", "type": "account_archived"})
    return account


@router.get("/accounts", response_model=ApiResponse)
async def list_accounts(db: AsyncSession = Depends(get_db)):
    repo = AccountTradingRepository(db)
    data = await repo.list_accounts()
    return ok(data, "账户列表已获取")


@router.post("/accounts", response_model=ApiResponse)
async def create_account(payload: AccountManageRequest, db: AsyncSession = Depends(get_db)):
    try:
        repo = AccountTradingRepository(db)
        data = await repo.create_account(payload.model_dump(exclude_unset=True))
        return ok(data, "账户已创建")
    except Exception as exc:
        raise_api_error(exc)


@router.put("/accounts/{account_id}", response_model=ApiResponse)
async def update_account(account_id: int, payload: AccountUpdateRequest, db: AsyncSession = Depends(get_db)):
    try:
        repo = AccountTradingRepository(db)
        data = await repo.update_account(account_id, payload.model_dump(exclude_unset=True))
        return ok(data, "账户已更新")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc), "type": "account_not_found"}) from exc
    except Exception as exc:
        raise_api_error(exc)


@router.post("/accounts/{account_id}/default", response_model=ApiResponse)
async def set_default_account(account_id: int, db: AsyncSession = Depends(get_db)):
    try:
        repo = AccountTradingRepository(db)
        data = await repo.set_default_account(account_id)
        return ok(data, "默认账户已更新")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc), "type": "account_not_found"}) from exc
    except Exception as exc:
        raise_api_error(exc)


@router.post("/accounts/{account_id}/archive", response_model=ApiResponse)
async def archive_account(account_id: int, db: AsyncSession = Depends(get_db)):
    try:
        repo = AccountTradingRepository(db)
        data = await repo.archive_account(account_id)
        return ok(data, "账户已归档")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc), "type": "account_not_found"}) from exc
    except Exception as exc:
        raise_api_error(exc)


@router.delete("/accounts/{account_id}", response_model=ApiResponse)
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    try:
        repo = AccountTradingRepository(db)
        data = await repo.archive_account(account_id)
        return ok(data, "账户已删除")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc), "type": "account_not_found"}) from exc
    except Exception as exc:
        raise_api_error(exc)


@router.get("/automation/status", response_model=ApiResponse)
async def automation_status(
    account_id: int | None = Query(default=None, gt=0),
    db: AsyncSession = Depends(get_db),
):
    try:
        repo = AccountTradingRepository(db)
        if account_id:
            account = await get_managed_account(repo, account_id)
            if account.account_type == "paper":
                data = {
                    "connected": True,
                    "ready": True,
                    "desktop_required": False,
                    "message": "当前选择的是系统模拟账户，使用本地模拟交易引擎，不检查同花顺桌面端。",
                }
                return ok(with_selected_account_context(data, account), "模拟交易引擎已就绪")
            if account.account_type == "backtest":
                data = {
                    "connected": True,
                    "ready": False,
                    "desktop_required": False,
                    "message": "当前选择的是回测账户，交易台手工下单暂不接入回测引擎。",
                }
                return ok(with_selected_account_context(data, account), "回测账户已识别")
            repo, account, data = await get_account_context(
                db, account_id, require_live=True, force_status=True
            )
            return ok(with_selected_account_context(data, account), "同花顺连接状态已获取")
        data = await run_in_threadpool(account_trading_service.status)
        account = await repo.ensure_account(data.get("account") or {})
        await repo.update_runtime_status(account, data)
        return ok(data, "同花顺连接状态已获取")
    except Exception as exc:
        raise_api_error(exc)


@router.post("/automation/connect", response_model=ApiResponse)
async def automation_connect(payload: AutomationConnectRequest | None = None, db: AsyncSession = Depends(get_db)):
    payload = payload or AutomationConnectRequest()
    try:
        repo = AccountTradingRepository(db)

        if payload.account_id:
            account = await get_managed_account(repo, payload.account_id)
            if account.account_type == "paper":
                data = {
                    "connected": True,
                    "ready": True,
                    "desktop_required": False,
                    "message": "当前选择的是系统模拟账户，使用本地模拟交易引擎，不启动同花顺桌面自动化。",
                }
                return ok(with_selected_account_context(data, account), "模拟交易引擎已就绪")
            if account.account_type == "backtest":
                data = {
                    "connected": True,
                    "ready": False,
                    "desktop_required": False,
                    "message": "当前选择的是回测账户，交易台手工下单暂不接入回测引擎。",
                }
                return ok(with_selected_account_context(data, account), "回测账户已识别")
            if account.account_type != "live":
                raise HTTPException(status_code=400, detail={"message": "当前账户类型暂不支持交易端连接。", "type": "unsupported_account_type"})

            client_path = await repo.get_active_client_path(account)
            data = await run_in_threadpool(account_trading_service.prepare_trading_workspace, client_path)
            assert_desktop_account_matches(account, data)
            await repo.update_runtime_status(account, data)
            return ok(
                with_selected_account_context(data, account),
                "同花顺交易端已连接，并按当前选择的实盘账户完成买入委托和资金查询页面检查",
            )

        data = await run_in_threadpool(account_trading_service.prepare_trading_workspace)
        account = await repo.ensure_account(data.get("account") or {})
        await repo.update_runtime_status(account, data)
        return ok(data, "同花顺交易端已连接，并完成买入委托和资金查询页面检查")
    except Exception as exc:
        raise_api_error(exc)


@router.get("/automation/logs", response_model=ApiResponse)
async def automation_logs(limit: int = Query(default=50, ge=1, le=200)):
    return ok(account_trading_service.logs(limit), "自动化日志已获取")


@router.get("/snapshot", response_model=ApiResponse)
async def get_account_snapshot(
    account_id: int = Query(..., gt=0),
    scope: OrderScope = "today",
    db: AsyncSession = Depends(get_db),
):
    try:
        repo = AccountTradingRepository(db)
        account = await get_managed_account(repo, account_id)
        await repo.expire_today_unfilled_orders(account)
        data = {
            "balance": await repo.get_latest_balance(account),
            "positions": await repo.list_latest_positions(account),
            "orders": await repo.list_order_records(account, scope),
            "trades": await repo.list_trade_records(account, scope),
        }
        return ok(data, "账户本地快照已获取")
    except Exception as exc:
        raise_api_error(exc)


@router.get("/positions/refresh", response_model=ApiResponse)
async def refresh_positions_with_realtime(
    account_id: int = Query(..., gt=0),
    db: AsyncSession = Depends(get_db),
):
    """刷新持仓：调用实时行情更新 last_price 并重新计算浮动盈亏。"""
    try:
        repo = AccountTradingRepository(db)
        account = await get_managed_account(repo, account_id)
        if account.account_type == "paper":
            result = await repo.refresh_paper_market_value(account)
            positions = result.get("positions", [])
        elif account.account_type == "live":
            await repo.expire_today_unfilled_orders(account)
            positions = await repo.list_latest_positions(account)
        else:
            await repo.expire_today_unfilled_orders(account)
            positions = await repo.list_latest_positions(account)
        return ok(positions, "持仓已刷新，实时行情已更新")
    except Exception as exc:
        raise_api_error(exc)


@router.get("/balance", response_model=ApiResponse)
async def get_balance(
    account_id: int | None = Query(default=None, gt=0),
    wait_manual_captcha: bool = False,
    manual_captcha_timeout: int = Query(default=120, ge=30, le=600),
    db: AsyncSession = Depends(get_db),
):
    try:
        repo = AccountTradingRepository(db)
        if account_id:
            account = await get_managed_account(repo, account_id)
            if account.account_type != "live":
                data = await repo.get_latest_balance(account)
                return ok(data, "账户余额已获取")
            repo, account, _ = await get_account_context(db, account_id, require_live=True)
        else:
            repo, account, _ = await get_account_context(db)
        task = await repo.create_task(
            account,
            "query_balance",
            {"wait_manual_captcha": wait_manual_captcha, "manual_captcha_timeout": manual_captcha_timeout},
        )
        client_path = await repo.get_active_client_path(account)
        data = await run_in_threadpool(
            account_trading_service.balance,
            client_path,
            wait_manual_captcha,
            manual_captcha_timeout,
        )
        await repo.save_balance(account, data)
        await repo.finish_task(task, "success", data)
        return ok(data, "账户余额已同步")
    except Exception as exc:
        raise_api_error(exc)


@router.get("/positions", response_model=ApiResponse)
async def get_positions(
    account_id: int | None = Query(default=None, gt=0),
    wait_manual_captcha: bool = False,
    manual_captcha_timeout: int = Query(default=120, ge=30, le=600),
    db: AsyncSession = Depends(get_db),
):
    try:
        repo = AccountTradingRepository(db)
        if account_id:
            account = await get_managed_account(repo, account_id)
            if account.account_type != "live":
                data = await repo.list_latest_positions(account)
                return ok(data, "持仓列表已获取")
            repo, account, _ = await get_account_context(db, account_id, require_live=True)
        else:
            repo, account, _ = await get_account_context(db)
        task = await repo.create_task(
            account,
            "query_positions",
            {"wait_manual_captcha": wait_manual_captcha, "manual_captcha_timeout": manual_captcha_timeout},
        )
        client_path = await repo.get_active_client_path(account)
        data = await run_in_threadpool(
            account_trading_service.positions,
            client_path,
            wait_manual_captcha,
            manual_captcha_timeout,
        )
        await repo.save_positions(account, data)
        await repo.finish_task(task, "success", {"count": len(data)})
        return ok(data, "持仓列表已同步")
    except Exception as exc:
        raise_api_error(exc)


@router.get("/orders", response_model=ApiResponse)
async def get_orders(
    scope: OrderScope = "today",
    account_id: int | None = Query(default=None, gt=0),
    wait_manual_captcha: bool = False,
    manual_captcha_timeout: int = Query(default=120, ge=30, le=600),
    db: AsyncSession = Depends(get_db),
):
    try:
        repo = AccountTradingRepository(db)
        if account_id:
            account = await get_managed_account(repo, account_id)
            if account.account_type == "live":
                repo, account, _ = await get_account_context(db, account_id, require_live=True)
        else:
            repo, account, _ = await get_account_context(db)
        task = await repo.create_task(
            account,
            "query_orders",
            {"scope": scope, "wait_manual_captcha": wait_manual_captcha, "manual_captcha_timeout": manual_captcha_timeout},
        )
        if account.account_type == "live":
            client_path = await repo.get_active_client_path(account)
            data = await run_in_threadpool(
                account_trading_service.orders,
                scope,
                client_path,
                wait_manual_captcha,
                manual_captcha_timeout,
            )
            await repo.save_orders(account, data)
        else:
            data = await repo.list_order_records(account, scope)
        await repo.finish_task(task, "success", {"count": len(data), "scope": scope, "account_id": account.id})
        return ok(data, "委托列表已同步")
    except Exception as exc:
        raise_api_error(exc)


@router.get("/trades", response_model=ApiResponse)
async def get_trades(
    scope: OrderScope = "today",
    account_id: int | None = Query(default=None, gt=0),
    wait_manual_captcha: bool = False,
    manual_captcha_timeout: int = Query(default=120, ge=30, le=600),
    db: AsyncSession = Depends(get_db),
):
    try:
        repo = AccountTradingRepository(db)
        if account_id:
            account = await get_managed_account(repo, account_id)
            if account.account_type == "live":
                repo, account, _ = await get_account_context(db, account_id, require_live=True)
        else:
            repo, account, _ = await get_account_context(db)
        task = await repo.create_task(
            account,
            "query_trades",
            {"scope": scope, "wait_manual_captcha": wait_manual_captcha, "manual_captcha_timeout": manual_captcha_timeout},
        )
        if account.account_type == "live":
            client_path = await repo.get_active_client_path(account)
            data = await run_in_threadpool(
                account_trading_service.trades,
                scope,
                client_path,
                wait_manual_captcha,
                manual_captcha_timeout,
            )
            await repo.save_trades(account, data)
        else:
            data = await repo.list_trade_records(account, scope)
        await repo.finish_task(task, "success", {"count": len(data), "scope": scope, "account_id": account.id})
        return ok(data, "成交列表已同步")
    except Exception as exc:
        raise_api_error(exc)


@router.post("/order", response_model=ApiResponse)
async def place_order(payload: OrderRequest, db: AsyncSession = Depends(get_db)):
    try:
        if payload.account_id:
            repo = AccountTradingRepository(db)
            account = await get_managed_account(repo, payload.account_id)
            selected_engine = engine_label(account.account_type)
        else:
            repo, account, _ = await get_account_context(db, None, require_live=True)
            selected_engine = engine_label(account.account_type)
        if payload.mode != account.account_type:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "请求交易模式与所选账户类型不一致，请刷新账户后重试。",
                    "type": "account_mode_mismatch",
                    "request_mode": payload.mode,
                    "account_type": account.account_type,
                },
            )
        if account.account_type == "live" and not payload.idempotency_key:
            raise HTTPException(
                status_code=400,
                detail={"message": "实盘下单必须提供 idempotency_key，避免重复下单。", "type": "missing_idempotency_key"},
            )

        if account.account_type == "paper":
            task = await repo.create_task(
                account,
                payload.side,
                {
                    "symbol": payload.symbol,
                    "name": payload.name,
                    "side": payload.side,
                    "price": str(payload.price),
                    "quantity": payload.quantity,
                    "mode": account.account_type,
                    "engine": selected_engine,
                    "account_id": payload.account_id,
                    "idempotency_key": payload.idempotency_key,
                    "remark": payload.remark,
                },
            )
            data = await repo.place_paper_order(
                account,
                side=payload.side,
                symbol=payload.symbol,
                name=payload.name,
                price=payload.price,
                quantity=payload.quantity,
                idempotency_key=payload.idempotency_key,
                remark=payload.remark,
            )
            await repo.finish_task(task, "success", data)
            status_message = "模拟委托已处理"
            if data.get("status") in {"filled", "partial_filled"}:
                status_message = "模拟委托已按页面委托价撮合成交"
            elif data.get("status") == "submitted":
                status_message = "模拟委托已提交，等待撮合"
            elif data.get("status") == "rejected":
                status_message = f"模拟委托已拒单：{data.get('reason') or '不满足交易条件'}"
            return ok(data, status_message)

        if account.account_type == "backtest":
            task = await repo.create_task(
                account,
                payload.side,
                {
                    "symbol": payload.symbol,
                    "side": payload.side,
                    "price": str(payload.price),
                    "quantity": payload.quantity,
                    "mode": account.account_type,
                    "engine": selected_engine,
                    "account_id": payload.account_id,
                    "idempotency_key": payload.idempotency_key,
                    "remark": payload.remark,
                },
            )
            error = RuntimeError("回测交易引擎需要绑定 backtest_run、策略和历史行情上下文，暂不支持从交易台手工下单。")
            await repo.finish_task(task, "failed", {"engine": selected_engine}, error)
            raise HTTPException(
                status_code=400,
                detail={
                    "message": str(error),
                    "type": "backtest_engine_requires_run",
                    "engine": selected_engine,
                },
            )

        if account.account_type != "live":
            raise HTTPException(status_code=400, detail={"message": "当前账户类型暂不支持交易台下单。", "type": "unsupported_account_type"})
        if payload.account_id:
            repo, account, _ = await get_account_context(db, payload.account_id, require_live=True)
        client_path = await repo.get_active_client_path(account)
        task = await repo.create_task(
            account,
            payload.side,
            {
                "symbol": payload.symbol,
                "side": payload.side,
                "price": str(payload.price),
                "quantity": payload.quantity,
                "mode": account.account_type,
                "engine": selected_engine,
                "account_id": payload.account_id,
                "idempotency_key": payload.idempotency_key,
                "remark": payload.remark,
            },
        )
        data = await run_in_threadpool(
            account_trading_service.place_order,
            side=payload.side,
            symbol=payload.symbol,
            price=payload.price,
            quantity=payload.quantity,
            client_path=client_path,
            wait_manual_captcha=payload.wait_manual_captcha,
            manual_captcha_timeout=payload.manual_captcha_timeout,
            idempotency_key=payload.idempotency_key,
            remark=payload.remark,
        )
        await repo.save_place_order_result(account, data)
        await repo.save_trades(account, data.get("matched_trades") or [])
        await repo.save_balance(account, data.get("balance_after") or {})
        confirmation_input = {
            "entrust_no": data.get("entrust_no") or (data.get("action_result") or {}).get("entrust_no"),
            "symbol": payload.symbol,
            "side": payload.side,
            "price": str(payload.price),
            "quantity": payload.quantity,
        }
        if skip_post_order_confirm():
            confirmation = {
                "confirmed": bool(confirmation_input["entrust_no"] or data.get("matched_orders") or data.get("matched_trades")),
                "final_status": data.get("status") or ("submitted" if confirmation_input["entrust_no"] else "unconfirmed"),
                "entrust_no": confirmation_input["entrust_no"],
                "matched_orders": data.get("matched_orders") or [],
                "matched_trades": data.get("matched_trades") or [],
                "orders": data.get("matched_orders") or [],
                "trades": data.get("matched_trades") or [],
                "balance": data.get("balance_after") or {},
                "errors": data.get("errors") or [],
                "skipped": True,
                "light_verify": True,
                "checked_at": None,
            }
            data["confirmation"] = confirmation
            await repo.append_task_step(
                task,
                "confirm_order",
                "success" if confirmation.get("confirmed") else "skipped",
                confirmation_input,
                {
                    "confirmed": confirmation.get("confirmed"),
                    "final_status": confirmation.get("final_status"),
                    "skipped": True,
                    "reason": "THS_SKIP_POST_ORDER_CONFIRM",
                },
                None,
            )
        else:
            try:
                confirmation = await run_in_threadpool(
                    account_trading_service.confirm_order,
                    entrust_no=confirmation_input["entrust_no"],
                    symbol=payload.symbol,
                    side=payload.side,
                    price=payload.price,
                    quantity=payload.quantity,
                    client_path=client_path,
                    wait_manual_captcha=payload.wait_manual_captcha,
                    manual_captcha_timeout=payload.manual_captcha_timeout,
                )
                data["confirmation"] = confirmation
                await repo.save_order_confirmation(
                    account,
                    confirmation,
                    event_type="order_confirm",
                    event_message="下单后订单确认回查完成",
                )
                await repo.save_balance(account, confirmation.get("balance") or {})
                await repo.append_task_step(
                    task,
                    "confirm_order",
                    "success" if confirmation.get("confirmed") else "failed",
                    confirmation_input,
                    {
                        "confirmed": confirmation.get("confirmed"),
                        "final_status": confirmation.get("final_status"),
                        "matched_orders": len(confirmation.get("matched_orders") or []),
                        "matched_trades": len(confirmation.get("matched_trades") or []),
                        "errors": confirmation.get("errors") or [],
                    },
                    None if confirmation.get("confirmed") else "订单未能在当日委托或成交中确认",
                )
            except Exception as confirm_exc:
                data["confirmation"] = {
                    "confirmed": False,
                    "final_status": "confirmation_failed",
                    "errors": [str(confirm_exc)],
                }
                await repo.append_task_step(
                    task,
                    "confirm_order",
                    "failed",
                    confirmation_input,
                    data["confirmation"],
                    str(confirm_exc),
                )
        await repo.finish_task(task, "success", data)
        return ok(data, "委托流程已执行，请以返回的合同编号、当日委托或成交回报为准")
    except Exception as exc:
        raise_api_error(exc)


@router.post("/order/{entrust_no}/cancel", response_model=ApiResponse)
async def cancel_order(entrust_no: str, payload: CancelRequest | None = None, db: AsyncSession = Depends(get_db)):
    payload = payload or CancelRequest()
    mode = payload.mode or "live"
    try:
        # ── 模拟盘撤单 ──
        if mode in ("paper", "backtest"):
            repo = AccountTradingRepository(db)
            account = await repo.get_account(payload.account_id)
            if not account:
                raise_api_error(ValueError(f"账户 {payload.account_id} 不存在"))
            result = await repo.cancel_paper_order(account, entrust_no)
            return ok(result, "模拟盘撤单成功")

        # ── 实盘撤单 ──
        repo, account, _ = await get_account_context(db, payload.account_id, require_live=True)
        client_path = await repo.get_active_client_path(account)
        task = await repo.create_task(
            account,
            "cancel",
            {"entrust_no": entrust_no, "wait_manual_captcha": payload.wait_manual_captcha},
        )
        data = await run_in_threadpool(
            account_trading_service.cancel_order,
            entrust_no,
            client_path,
            payload.wait_manual_captcha,
            payload.manual_captcha_timeout,
        )
        await repo.save_cancel_result(account, entrust_no, data)
        await repo.save_balance(account, data.get("balance_after") or {})
        confirmation_input = {"entrust_no": entrust_no}
        try:
            confirmation = await run_in_threadpool(
                account_trading_service.confirm_order,
                entrust_no=entrust_no,
                client_path=client_path,
                wait_manual_captcha=payload.wait_manual_captcha,
                manual_captcha_timeout=payload.manual_captcha_timeout,
            )
            data["confirmation"] = confirmation
            final_status = _normalize_order_status(confirmation.get("final_status"))
            await repo.save_order_confirmation(
                account,
                confirmation,
                event_type="cancel_confirm",
                event_message="撤单后订单确认回查完成",
            )
            await repo.save_balance(account, confirmation.get("balance") or {})
            await repo.append_task_step(
                task,
                "confirm_cancel",
                "success" if confirmation.get("confirmed") else "failed",
                confirmation_input,
                {
                    "confirmed": confirmation.get("confirmed"),
                    "final_status": confirmation.get("final_status"),
                    "matched_orders": len(confirmation.get("matched_orders") or []),
                    "matched_trades": len(confirmation.get("matched_trades") or []),
                    "errors": confirmation.get("errors") or [],
                },
                None if confirmation.get("confirmed") else "撤单结果未能在当日委托中确认",
            )
            if final_status not in {"cancel_pending", "partial_canceled", "canceled"}:
                await repo.finish_task(
                    task,
                    "failed",
                    {
                        **data,
                        "confirmation": confirmation,
                        "failure_reason": "撤单后回查同花顺，当日委托状态没有变为撤单中、部分撤单或全部撤单。",
                    },
                )
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "撤单动作未在同花顺当日委托中确认成功，当前状态仍不是撤单中/部分撤单/全部撤单。请先同步核对该委托是否仍可撤。",
                        "type": "cancel_not_confirmed",
                        "entrust_no": entrust_no,
                        "final_status": final_status,
                        "confirmation": confirmation,
                    },
                )
        except Exception as confirm_exc:
            if isinstance(confirm_exc, HTTPException):
                raise
            data["confirmation"] = {
                "confirmed": False,
                "final_status": "confirmation_failed",
                "errors": [str(confirm_exc)],
            }
            await repo.append_task_step(
                task,
                "confirm_cancel",
                "failed",
                confirmation_input,
                data["confirmation"],
                str(confirm_exc),
            )
            await repo.finish_task(task, "failed", data)
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "撤单动作已执行，但撤单结果回查失败，不能判定为成功。请点击同步核对同花顺当日委托状态。",
                    "type": "cancel_confirmation_failed",
                    "entrust_no": entrust_no,
                    "confirmation": data["confirmation"],
                },
            ) from confirm_exc
        await repo.finish_task(task, "success", data)
        return ok(data, "撤单已在同花顺当日委托中确认")
    except Exception as exc:
        raise_api_error(exc)


@router.post("/orders/cancel-all", response_model=ApiResponse)
async def cancel_all_orders(payload: CancelRequest | None = None, db: AsyncSession = Depends(get_db)):
    payload = payload or CancelRequest()
    try:
        repo, account, _ = await get_account_context(db, payload.account_id, require_live=True)
        client_path = await repo.get_active_client_path(account)
        task = await repo.create_task(
            account,
            "cancel",
            {"scope": "all_cancellable", "wait_manual_captcha": payload.wait_manual_captcha},
        )
        data = await run_in_threadpool(
            account_trading_service.cancel_all_orders,
            client_path,
            payload.wait_manual_captcha,
            payload.manual_captcha_timeout,
        )
        await repo.save_balance(account, data.get("balance_after") or {})
        await repo.finish_task(task, "success", data)
        return ok(data, "全部撤单流程已执行，请立即同步当日委托确认状态")
    except Exception as exc:
        raise_api_error(exc)


@router.post("/sync", response_model=ApiResponse)
async def sync_account(payload: SyncRequest | None = None, db: AsyncSession = Depends(get_db)):
    payload = payload or SyncRequest()
    try:
        if payload.account_id:
            repo = AccountTradingRepository(db)
            account = await get_managed_account(repo, payload.account_id)
            if account.account_type == "paper":
                match_result = await repo.match_pending_paper_orders(account)
                await repo.expire_today_unfilled_orders(account)
                refreshed = await repo.refresh_paper_market_value(account)
                data = {
                    "balance": refreshed["balance"],
                    "positions": refreshed["positions"],
                    "orders": await repo.list_order_records(account, "today"),
                    "trades": await repo.list_trade_records(account, "today"),
                    "match_result": match_result,
                }
                return ok(data, "账户数据已获取")
            if account.account_type != "live":
                raise HTTPException(status_code=400, detail={"message": "当前账户类型暂不支持交易台同步。", "type": "unsupported_account_type"})
        repo, account, _ = await get_account_context(db, payload.account_id, require_live=True)
        task = await repo.create_task(
            account,
            "sync",
            {"wait_manual_captcha": payload.wait_manual_captcha, "manual_captcha_timeout": payload.manual_captcha_timeout},
        )
        client_path = await repo.get_active_client_path(account)
        data = await run_in_threadpool(
            account_trading_service.sync,
            client_path,
            payload.wait_manual_captcha,
            payload.manual_captcha_timeout,
        )
        await repo.save_balance(account, data.get("balance") or {})
        await repo.save_positions(account, data.get("positions") or [])
        await repo.save_orders(account, data.get("orders") or [])
        await repo.save_trades(account, data.get("trades") or [])
        await repo.finish_task(
            task,
            "success",
            {
                "positions": len(data.get("positions") or []),
                "orders": len(data.get("orders") or []),
                "trades": len(data.get("trades") or []),
            },
        )
        return ok(data, "账户数据已同步")
    except Exception as exc:
        raise_api_error(exc)


@router.post("/paper/refresh-market-value", response_model=ApiResponse)
async def refresh_paper_market_value(payload: SyncRequest, db: AsyncSession = Depends(get_db)):
    try:
        if not payload.account_id:
            raise HTTPException(status_code=400, detail={"message": "刷新模拟持仓市值必须选择模拟账户。", "type": "account_required"})
        repo = AccountTradingRepository(db)
        account = await get_managed_account(repo, payload.account_id)
        if account.account_type != "paper":
            raise HTTPException(status_code=400, detail={"message": "当前选择的账户不是模拟账户。", "type": "unsupported_account_type"})
        data = await repo.refresh_paper_market_value(account)
        return ok(data, "模拟账户持仓市值和资金快照已刷新")
    except Exception as exc:
        raise_api_error(exc)
