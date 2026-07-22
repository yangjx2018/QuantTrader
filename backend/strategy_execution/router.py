from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from common.dependencies import get_db
from common.utils.response import ApiResponse

from .models import Execution, ExecutionSignal, RiskAlert, RiskRule, ExecutionLog
from .schemas import (
    Execution as ExecutionSchema,
    ExecutionSignal as ExecutionSignalSchema,
    RiskAlert as RiskAlertSchema,
    RiskRule as RiskRuleSchema,
    ExecutionLog as ExecutionLogSchema,
    ExecutionStatusResponse,
    StartExecutionRequest,
)


router = APIRouter(prefix="/api/execution", tags=["策略执行与风控"])


class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int


def ok(data: Any = None, message: str = "ok") -> ApiResponse:
    return ApiResponse(success=True, data=data, message=message)


def paginate(items: list[Any], total: int, page: int, page_size: int) -> PaginatedResponse:
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/status", response_model=ApiResponse)
async def get_execution_status(db: AsyncSession = Depends(get_db)):
    """获取执行状态统计"""
    result = await db.execute(
        select(
            func.sum(func.if_(Execution.status == "running", 1, 0)),
            func.sum(func.if_(Execution.status == "paused", 1, 0)),
            func.sum(func.if_(Execution.status == "stopped", 1, 0)),
            func.sum(Execution.total_pnl),
            func.sum(func.if_(RiskAlert.acknowledged == False, 1, 0)),  # noqa: E712
        ).select_from(Execution).outerjoin(RiskAlert, Execution.id == RiskAlert.execution_id)
    )
    row = result.first() or (0, 0, 0, 0, 0)

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    signal_result = await db.execute(
        select(func.count(ExecutionSignal.id)).where(ExecutionSignal.created_at >= today_start)
    )
    total_signals_today = signal_result.scalar() or 0

    status = ExecutionStatusResponse(
        running_count=int(row[0] or 0),
        paused_count=int(row[1] or 0),
        stopped_count=int(row[2] or 0),
        today_pnl=float(row[3] or 0),
        active_alerts=int(row[4] or 0),
        total_signals_today=total_signals_today,
    )
    return ok(status, "执行状态已获取")


@router.get("/list", response_model=ApiResponse)
async def get_execution_list(
    status: str | None = Query(default=None),
    strategy_id: int | None = Query(default=None, gt=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """获取执行实例列表"""
    query = select(Execution).order_by(Execution.start_time.desc())
    count_query = select(func.count(Execution.id))

    if status:
        query = query.where(Execution.status == status)
        count_query = count_query.where(Execution.status == status)
    if strategy_id:
        query = query.where(Execution.strategy_id == strategy_id)
        count_query = count_query.where(Execution.strategy_id == strategy_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    executions = result.scalars().all()

    items = [ExecutionSchema.model_validate(exec) for exec in executions]
    return ok(paginate(items, total, page, page_size), "执行列表已获取")


@router.post("/start", response_model=ApiResponse)
async def start_execution(payload: StartExecutionRequest, db: AsyncSession = Depends(get_db)):
    """启动策略执行：校验策略+账户，写入运行态，并注册到后台 Worker。"""
    from fastapi import HTTPException

    from account_trading.repository import AccountTradingRepository
    from strategy_engine.exceptions import StrategyNotActive, StrategyNotFound
    from strategy_engine.repository import StrategyRepository
    from strategy_engine.service import load_strategy
    from strategy_execution.worker import get_execution_worker

    params = dict(payload.params or {})
    symbol = str(params.get("symbol") or "").strip()
    if not symbol:
        raise HTTPException(status_code=400, detail={"message": "params.symbol 必填（如 000001.SZ）", "type": "invalid_request"})

    try:
        await load_strategy(db, payload.strategy_id)
    except StrategyNotFound as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc), "type": "strategy_not_found"}) from exc
    except StrategyNotActive as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc), "type": "strategy_not_active"}) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"message": f"策略加载失败: {exc}", "type": "strategy_load_error"}) from exc

    strategy_repo = StrategyRepository(db)
    strategy = await strategy_repo.get_by_id(payload.strategy_id)
    strategy_name = strategy.name if strategy else f"策略-{payload.strategy_id}"

    account_repo = AccountTradingRepository(db)
    account = await account_repo.get_account(int(payload.account_id))
    if account is None:
        raise HTTPException(status_code=404, detail={"message": f"交易账户不存在: {payload.account_id}", "type": "account_not_found"})
    if account.account_type not in {"paper", "live"}:
        raise HTTPException(
            status_code=400,
            detail={"message": f"执行仅支持 paper/live 账户，当前为 {account.account_type}", "type": "unsupported_account_type"},
        )
    if account.status == "archived":
        raise HTTPException(status_code=400, detail={"message": "账户已归档，无法用于执行", "type": "account_archived"})

    params.setdefault("timeframe", "1d")
    params["symbol"] = symbol

    execution = Execution(
        strategy_id=str(payload.strategy_id),
        strategy_name=strategy_name,
        account_id=int(payload.account_id),
        status="running",
        start_time=datetime.now(),
        params=params,
    )
    db.add(execution)
    try:
        await db.flush()
        db.add(
            ExecutionLog(
                execution_id=execution.id,
                level="info",
                category="execution",
                message=(
                    f"策略执行已启动: {strategy_name} → 账户#{account.id}({account.account_type}) "
                    f"标的={symbol}"
                ),
                details={"strategy_id": payload.strategy_id, "account_id": account.id, "symbol": symbol},
            )
        )
        await db.commit()
        await db.refresh(execution)
    except Exception as exc:
        await db.rollback()
        err = str(exc)
        if "1290" in err or "LOCK_WRITE" in err or "read only" in err.lower():
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "数据库当前只读（LOCK_WRITE），无法启动执行。请解除 RDS 写锁或切换可写库后重试。",
                    "type": "db_write_locked",
                },
            ) from exc
        raise

    worker = get_execution_worker()
    await worker.register(execution)
    try:
        tick_summary = await worker.force_tick(execution.id)
    except Exception as exc:
        tick_summary = {"error": str(exc)}
        db.add(
            ExecutionLog(
                execution_id=execution.id,
                level="warning",
                category="execution",
                message=f"首轮 tick 未完成: {exc}",
            )
        )
        await db.commit()

    await db.refresh(execution)
    data = ExecutionSchema.model_validate(execution).model_dump()
    data["first_tick"] = tick_summary
    return ok(data, "策略执行已启动并接入交易链路")


@router.post("/{execution_id:int}/stop", response_model=ApiResponse)
async def stop_execution(execution_id: int, db: AsyncSession = Depends(get_db)):
    """停止策略执行"""
    from fastapi import HTTPException
    from strategy_execution.worker import get_execution_worker

    execution = await db.get(Execution, execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail={"message": "执行实例不存在", "type": "not_found"})

    execution.status = "stopped"
    execution.end_time = datetime.now()
    db.add(ExecutionLog(execution_id=execution.id, level="info", category="execution", message="策略执行已停止"))
    await db.commit()
    await db.refresh(execution)
    await get_execution_worker().unregister(execution_id)
    return ok(ExecutionSchema.model_validate(execution), "策略执行已停止")


@router.post("/{execution_id:int}/pause", response_model=ApiResponse)
async def pause_execution(execution_id: int, db: AsyncSession = Depends(get_db)):
    """暂停策略执行"""
    from fastapi import HTTPException

    execution = await db.get(Execution, execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail={"message": "执行实例不存在", "type": "not_found"})

    execution.status = "paused"
    db.add(ExecutionLog(execution_id=execution.id, level="info", category="execution", message="策略执行已暂停"))
    await db.commit()
    await db.refresh(execution)
    return ok(ExecutionSchema.model_validate(execution), "策略执行已暂停")


@router.post("/{execution_id:int}/resume", response_model=ApiResponse)
async def resume_execution(execution_id: int, db: AsyncSession = Depends(get_db)):
    """恢复策略执行"""
    from fastapi import HTTPException
    from strategy_execution.worker import get_execution_worker

    execution = await db.get(Execution, execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail={"message": "执行实例不存在", "type": "not_found"})

    execution.status = "running"
    db.add(ExecutionLog(execution_id=execution.id, level="info", category="execution", message="策略执行已恢复"))
    await db.commit()
    await db.refresh(execution)
    await get_execution_worker().register(execution)
    return ok(ExecutionSchema.model_validate(execution), "策略执行已恢复")


@router.get("/{execution_id:int}", response_model=ApiResponse)
async def get_execution_detail(execution_id: int, db: AsyncSession = Depends(get_db)):
    """获取执行实例详情"""
    from fastapi import HTTPException

    execution = await db.get(Execution, execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail={"message": "执行实例不存在", "type": "not_found"})
    return ok(ExecutionSchema.model_validate(execution), "执行详情已获取")


@router.post("/{execution_id:int}/tick", response_model=ApiResponse)
async def force_execution_tick(execution_id: int, db: AsyncSession = Depends(get_db)):
    """手动触发一次策略 tick（跑信号并尝试下单）。"""
    from fastapi import HTTPException
    from strategy_execution.worker import get_execution_worker

    execution = await db.get(Execution, execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail={"message": "执行实例不存在", "type": "not_found"})
    if execution.status != "running":
        raise HTTPException(status_code=400, detail={"message": "仅 running 状态可触发 tick", "type": "invalid_status"})

    try:
        summary = await get_execution_worker().force_tick(execution_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"message": str(exc), "type": "tick_failed"}) from exc
    return ok(summary, "已触发策略 tick")


@router.post("/{execution_id:int}/mock-signal", response_model=ApiResponse)
async def generate_mock_signal(execution_id: int, db: AsyncSession = Depends(get_db)):
    """兼容旧接口：改为真实 tick（不再生成随机假信号）。"""
    return await force_execution_tick(execution_id, db)


@router.get("/{execution_id:int}/signals", response_model=ApiResponse)
async def get_execution_signals(
    execution_id: int,
    risk_passed: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """获取执行实例的信号列表"""
    query = select(ExecutionSignal).where(ExecutionSignal.execution_id == execution_id).order_by(ExecutionSignal.created_at.desc())
    count_query = select(func.count(ExecutionSignal.id)).where(ExecutionSignal.execution_id == execution_id)

    if risk_passed is not None:
        query = query.where(ExecutionSignal.risk_passed == risk_passed)
        count_query = count_query.where(ExecutionSignal.risk_passed == risk_passed)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    signals = result.scalars().all()

    items = [ExecutionSignalSchema.model_validate(sig) for sig in signals]
    return ok(paginate(items, total, page, page_size), "信号列表已获取")


@router.get("/signals", response_model=ApiResponse)
async def get_all_signals(
    execution_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """获取所有交易信号"""
    query = select(ExecutionSignal).order_by(ExecutionSignal.created_at.desc())
    if execution_id:
        query = query.where(ExecutionSignal.execution_id == execution_id)

    query = query.limit(limit)
    result = await db.execute(query)
    signals = result.scalars().all()

    items = [ExecutionSignalSchema.model_validate(sig) for sig in signals]
    return ok(items, "信号列表已获取")


@router.get("/risk-rules", response_model=ApiResponse)
async def get_risk_rules(
    enabled: bool | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """获取风控规则列表"""
    query = select(RiskRule).order_by(RiskRule.id)
    if enabled is not None:
        query = query.where(RiskRule.enabled == enabled)

    result = await db.execute(query)
    rules = result.scalars().all()

    if not rules:
        default_rules = [
            RiskRule(
                rule_type="max_daily_loss",
                rule_name="单日最大亏损",
                enabled=True,
                params={"threshold": -0.05},
                description="单日亏损超过5%触发",
            ),
            RiskRule(
                rule_type="max_position_size",
                rule_name="单票最大仓位",
                enabled=True,
                params={"threshold": 0.3},
                description="单只股票仓位不超过30%",
            ),
            RiskRule(
                rule_type="max_drawdown",
                rule_name="最大回撤",
                enabled=True,
                params={"threshold": -0.15},
                description="回撤超过15%触发",
            ),
        ]
        for rule in default_rules:
            db.add(rule)
        await db.commit()
        result = await db.execute(query)
        rules = result.scalars().all()

    items = [RiskRuleSchema.model_validate(rule) for rule in rules]
    return ok(items, "风控规则已获取")


@router.post("/risk-rules", response_model=ApiResponse)
async def create_risk_rule(payload: dict, db: AsyncSession = Depends(get_db)):
    """创建风控规则"""
    rule = RiskRule(
        rule_type=payload.get("rule_type", ""),
        rule_name=payload.get("rule_name", ""),
        enabled=payload.get("enabled", True),
        params=payload.get("params", {}),
        description=payload.get("description"),
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return ok(RiskRuleSchema.model_validate(rule), "风控规则已创建")


@router.get("/risk-rules/{rule_id}", response_model=ApiResponse)
async def get_risk_rule_detail(rule_id: int, db: AsyncSession = Depends(get_db)):
    """获取风控规则详情"""
    rule = await db.get(RiskRule, rule_id)
    if not rule:
        return ok(None, "风控规则不存在")
    return ok(RiskRuleSchema.model_validate(rule), "风控规则详情已获取")


@router.put("/risk-rules/{rule_id}", response_model=ApiResponse)
async def update_risk_rule(rule_id: int, payload: dict, db: AsyncSession = Depends(get_db)):
    """更新风控规则"""
    rule = await db.get(RiskRule, rule_id)
    if not rule:
        return ok(None, "风控规则不存在")

    if "rule_name" in payload:
        rule.rule_name = payload["rule_name"]
    if "enabled" in payload:
        rule.enabled = payload["enabled"]
    if "params" in payload:
        rule.params = payload["params"]
    if "description" in payload:
        rule.description = payload["description"]

    await db.commit()
    await db.refresh(rule)
    return ok(RiskRuleSchema.model_validate(rule), "风控规则已更新")


@router.delete("/risk-rules/{rule_id}", response_model=ApiResponse)
async def delete_risk_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    """删除风控规则"""
    rule = await db.get(RiskRule, rule_id)
    if not rule:
        return ok(None, "风控规则不存在")

    await db.delete(rule)
    await db.commit()
    return ok(None, "风控规则已删除")


@router.post("/risk-rules/{rule_id}/enable", response_model=ApiResponse)
async def enable_risk_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    """启用风控规则"""
    rule = await db.get(RiskRule, rule_id)
    if not rule:
        return ok(None, "风控规则不存在")

    rule.enabled = True
    await db.commit()
    await db.refresh(rule)
    return ok(RiskRuleSchema.model_validate(rule), "风控规则已启用")


@router.post("/risk-rules/{rule_id}/disable", response_model=ApiResponse)
async def disable_risk_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    """禁用风控规则"""
    rule = await db.get(RiskRule, rule_id)
    if not rule:
        return ok(None, "风控规则不存在")

    rule.enabled = False
    await db.commit()
    await db.refresh(rule)
    return ok(RiskRuleSchema.model_validate(rule), "风控规则已禁用")


@router.get("/risk-alerts/active", response_model=ApiResponse)
async def get_active_risk_alerts(db: AsyncSession = Depends(get_db)):
    """获取活跃风控告警"""
    query = (
        select(RiskAlert)
        .where(RiskAlert.acknowledged == False)  # noqa: E712
        .order_by(RiskAlert.created_at.desc())
    )
    result = await db.execute(query)
    alerts = result.scalars().all()

    if not alerts:
        return ok([], "暂无活跃告警")

    items = [RiskAlertSchema.model_validate(alert) for alert in alerts]
    return ok(items, "活跃告警已获取")


@router.get("/risk-alerts", response_model=ApiResponse)
async def get_risk_alerts(
    execution_id: int | None = Query(default=None, gt=0),
    level: str | None = Query(default=None),
    acknowledged: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """获取风控告警列表"""
    query = select(RiskAlert).order_by(RiskAlert.created_at.desc())
    count_query = select(func.count(RiskAlert.id))

    if execution_id:
        query = query.where(RiskAlert.execution_id == execution_id)
        count_query = count_query.where(RiskAlert.execution_id == execution_id)
    if level:
        query = query.where(RiskAlert.severity == level)
        count_query = count_query.where(RiskAlert.severity == level)
    if acknowledged is not None:
        query = query.where(RiskAlert.acknowledged == acknowledged)
        count_query = count_query.where(RiskAlert.acknowledged == acknowledged)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    alerts = result.scalars().all()

    items = [RiskAlertSchema.model_validate(alert) for alert in alerts]
    return ok(paginate(items, total, page, page_size), "告警列表已获取")


@router.post("/risk-alerts/{alert_id}/acknowledge", response_model=ApiResponse)
async def acknowledge_risk_alert(alert_id: int, db: AsyncSession = Depends(get_db)):
    """确认风控告警"""
    alert = await db.get(RiskAlert, alert_id)
    if not alert:
        return ok(None, "告警不存在")

    alert.acknowledged = True
    alert.acknowledged_at = datetime.now()
    alert.acknowledged_by = "system"

    await db.commit()
    await db.refresh(alert)
    return ok(RiskAlertSchema.model_validate(alert), "告警已确认")


@router.get("/{execution_id:int}/logs", response_model=ApiResponse)
async def get_execution_logs(
    execution_id: int,
    level: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """获取执行日志"""
    query = (
        select(ExecutionLog)
        .where(ExecutionLog.execution_id == execution_id)
        .order_by(ExecutionLog.created_at.desc())
    )
    count_query = select(func.count(ExecutionLog.id)).where(ExecutionLog.execution_id == execution_id)

    if level:
        query = query.where(ExecutionLog.level == level)
        count_query = count_query.where(ExecutionLog.level == level)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    logs = result.scalars().all()

    items = [ExecutionLogSchema.model_validate(log) for log in logs]
    return ok(paginate(items, total, page, page_size), "执行日志已获取")
