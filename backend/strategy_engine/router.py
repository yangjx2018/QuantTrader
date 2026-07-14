"""strategy_engine 模块的 FastAPI 路由。

变更说明：
- 从 `._db` 改为 `.models` / `common.database.get_db`
- 删除字段引用 entry_rules/exit_rules/risk_rules
- DELETE 端点级联删除 strategy_version
- 新增 /options、/validate、/{id}/dry-run 路由（6.x/7.x 任务，提前预留）
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from common.database import get_db

from .exceptions import BacktestError, StrategyNotFound, StrategyNotActive, InvalidStrategyError, StrategyLoadError
from .models import Strategy, StrategyVersion
from .repository import StrategyRepository, StrategyVersionRepository
from .schemas import (
    BatchBacktestRequest,
    BatchBacktestResponse,
    CompareRequest,
    CompareResponse,
    DryRunRequest,
    DryRunResponse,
    ExportRequest,
    ExportResponse,
    ImportRequest,
    ImportResponse,
    StrategyCreate,
    StrategyOption,
    StrategyResponse,
    StrategyUpdate,
    StrategyValidateRequest,
    StrategyValidateResponse,
    StrategyVersionCreate,
    StrategyVersionResponse,
)


router = APIRouter(prefix="/api/strategy", tags=["策略引擎"])


# ============================================================
# 健康检查
# ============================================================

@router.get("/list", response_model=dict)
async def get_strategies(
    status: Optional[str] = Query(None, description="状态过滤"),
    strategy_type: Optional[str] = Query(None, description="类型过滤"),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """获取策略列表。"""
    repo = StrategyRepository(db)
    strategies = await repo.list_all(
        status=status, strategy_type=strategy_type, limit=limit, offset=offset
    )
    total = await repo.count(status=status)
    return {
        "success": True,
        "data": [StrategyResponse.model_validate(s).model_dump() for s in strategies],
        "total": total,
        "message": "success",
    }


@router.post("/create", response_model=dict)
async def create_strategy(
    data: StrategyCreate,
    db: AsyncSession = Depends(get_db),
):
    """创建策略。"""
    if not data.code_content or not data.code_content.strip():
        raise HTTPException(status_code=400, detail="策略代码不能为空")

    repo = StrategyRepository(db)
    existing = await repo.get_by_code(data.code)
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"策略编码 {data.code} 已存在",
        )

    strategy = await repo.create(data.model_dump())
    return {
        "success": True,
        "data": StrategyResponse.model_validate(strategy).model_dump(),
        "message": "策略创建成功",
    }


@router.get("/{strategy_id}", response_model=dict)
async def get_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取策略详情。"""
    repo = StrategyRepository(db)
    strategy = await repo.get_by_id(strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")
    return {
        "success": True,
        "data": StrategyResponse.model_validate(strategy).model_dump(),
        "message": "success",
    }


@router.put("/{strategy_id}", response_model=dict)
async def update_strategy(
    strategy_id: int,
    data: StrategyUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新策略。"""
    repo = StrategyRepository(db)
    strategy = await repo.get_by_id(strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    update_data = data.model_dump(exclude_unset=True)
    updated = await repo.update(strategy_id, update_data)

    # P3: WebSocket 推送 code_updated
    from .runtime.websocket import get_ws_manager
    manager = get_ws_manager()
    await manager.broadcast(
        strategy_id,
        {
            "type": "code_updated",
            "strategy_id": strategy_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {
        "success": True,
        "data": StrategyResponse.model_validate(updated).model_dump(),
        "message": "策略更新成功",
    }


@router.delete("/{strategy_id}", response_model=dict)
async def delete_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除策略（级联删除历史版本）。"""
    repo = StrategyRepository(db)
    strategy = await repo.get_by_id(strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    # 级联删除版本
    version_repo = StrategyVersionRepository(db)
    deleted_versions = await version_repo.delete_by_strategy(strategy_id)

    await repo.delete(strategy_id)

    # P3: WebSocket 推送 strategy_deleted
    from .runtime.websocket import get_ws_manager
    manager = get_ws_manager()
    await manager.broadcast(
        strategy_id,
        {
            "type": "strategy_deleted",
            "strategy_id": strategy_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {
        "success": True,
        "data": {"deleted_versions": deleted_versions},
        "message": "策略删除成功",
    }


# ============================================================
# 策略版本
# ============================================================

@router.get("/{strategy_id}/versions", response_model=dict)
async def get_strategy_versions(
    strategy_id: int,
    status: Optional[str] = Query(None, description="版本状态过滤"),
    db: AsyncSession = Depends(get_db),
):
    """获取策略版本历史。"""
    repo = StrategyRepository(db)
    strategy = await repo.get_by_id(strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    version_repo = StrategyVersionRepository(db)
    versions = await version_repo.list_by_strategy(strategy_id, status=status)
    return {
        "success": True,
        "data": [StrategyVersionResponse.model_validate(v).model_dump() for v in versions],
        "message": "success",
    }


@router.post("/{strategy_id}/versions", response_model=dict)
async def create_strategy_version(
    strategy_id: int,
    data: StrategyVersionCreate,
    db: AsyncSession = Depends(get_db),
):
    """创建策略新版本。"""
    repo = StrategyRepository(db)
    strategy = await repo.get_by_id(strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    version_data = data.model_dump()
    version_data["strategy_id"] = strategy_id

    version_repo = StrategyVersionRepository(db)
    try:
        version = await version_repo.create(version_data)
    except Exception as e:
        # UNIQUE(strategy_id, version) 冲突等
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"版本创建失败: {e}",
        )

    await repo.update(strategy_id, {"version": data.version})

    return {
        "success": True,
        "data": StrategyVersionResponse.model_validate(version).model_dump(),
        "message": "版本创建成功",
    }


# ============================================================
# 新增：简化列表 / 校验 / 试运行（6.x/7.x 任务接口提前预留）
# ============================================================

@router.get("/options/all", response_model=dict)
async def get_strategy_options(
    status: Optional[str] = Query("active", description="默认只返回 active 策略；传 all 返回全部"),
    db: AsyncSession = Depends(get_db),
):
    """获取策略简化列表（给 history_replay 等模块调用）。

    GET /api/strategy/options/all?status=active
    GET /api/strategy/options/all?status=all
    """
    # 注意：路径 /options/all 而不是 /options，避免与 /{strategy_id} 冲突
    repo = StrategyRepository(db)
    filter_status = None if status == "all" else status
    strategies = await repo.list_options(status=filter_status)
    return {
        "success": True,
        "data": [
            StrategyOption(
                id=s.id, name=s.name,
                description=s.description, strategy_type=s.strategy_type,
            ).model_dump()
            for s in strategies
        ],
        "message": "success",
    }


@router.post("/validate", response_model=dict)
async def validate_strategy_code(
    payload: StrategyValidateRequest,
):
    """校验策略代码语法 + 沙箱可加载性。不查 DB。"""
    # 延迟 import 避免循环（loader 不依赖 router）
    from .runtime.loader import validate_code

    result = validate_code(payload.code_content)
    return {
        "success": True,
        "data": result,
        "message": "校验完成",
    }


@router.post("/{strategy_id}/dry-run", response_model=dict)
async def dry_run_strategy(
    strategy_id: int,
    payload: DryRunRequest,
    db: AsyncSession = Depends(get_db),
):
    """策略试运行：基于 mock V 形趋势数据，跑最多 100 个 bar 的回测。

    业务用途：
      用户在策略编辑器写完代码后点"试运行"，快速验证：
      - 代码能否在沙箱加载
      - 是否能产生订单
      - 是否有运行时异常

    数据特征：
      mock 数据为固定 V 形趋势（跌 15 天 → 涨 25 天 → 震荡 20 天），
      足以触发 4 个内置策略的交易信号。**不依赖真实行情**。

    限制：
      - max_bars 上限 100（避免阻塞太久）
      - 仅 timeframe="1d" 支持
    """
    from .exceptions import (
        InvalidStrategyError,
        StrategyNotActive,
        StrategyNotFound,
    )
    from .service import dry_run

    try:
        result = await dry_run(
            db=db,
            strategy_id=strategy_id,
            stock_code=payload.stock_code,
            start_date=payload.start_date,
            end_date=payload.end_date,
            max_bars=payload.max_bars,
            parameters=payload.parameters,
        )
    except StrategyNotFound as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    except StrategyNotActive as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    except InvalidStrategyError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    return {
        "success": True,
        "data": result.model_dump(),
        "message": "试运行完成",
    }


# ============================================================
# 实盘对接：/reload 路由
# ============================================================

@router.post("/{strategy_id}/reload", response_model=dict)
async def reload_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
):
    """热加载策略实例（清除缓存并重新加载）。

    用于策略代码更新后，强制重新加载策略实例。
    """
    from strategy_engine.runtime.registry import get_global_registry
    from .service import load_strategy

    registry = get_global_registry()

    # 清除缓存
    await registry.evict(strategy_id)

    # 重新加载
    try:
        instance = await load_strategy(db, strategy_id)
        return {
            "success": True,
            "data": {
                "strategy_id": strategy_id,
                "status": "reloaded",
            },
            "message": "策略已重新加载",
        }
    except StrategyNotFound:
        raise HTTPException(status_code=404, detail=f"策略 {strategy_id} 不存在")
    except StrategyNotActive as e:
        raise HTTPException(status_code=400, detail=str(e))
    except InvalidStrategyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except StrategyLoadError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# P3: 策略热更新 WebSocket
# ============================================================

@router.websocket("/ws/strategy/{strategy_id}")
async def websocket_strategy_endpoint(
    websocket: WebSocket,
    strategy_id: int,
):
    """WebSocket 端点：订阅策略代码变更推送。

    连接后接收：
      - code_updated: 策略代码更新
      - strategy_deleted: 策略被删除

    客户端应响应 ping/pong 保持连接。
    """
    from .runtime.websocket import get_ws_manager

    manager = get_ws_manager()
    await websocket.accept()
    await manager.handle_connection(websocket, strategy_id)


# ============================================================
# P3: 并发回测
# ============================================================

@router.post(
    "/batch-backtest",
    response_model=dict,
    status_code=200,
    tags=["策略引擎"],
    summary="并发回测多策略",
)
async def batch_backtest_endpoint(
    req: BatchBacktestRequest,
    db: AsyncSession = Depends(get_db),
):
    """并发执行多个策略回测，最多 10 个。"""
    from .exceptions import InvalidStrategyError, StrategyNotActive, StrategyNotFound
    from .service import batch_backtest

    if len(req.strategy_ids) > 10:
        raise HTTPException(
            status_code=400,
            detail="并发回测最多支持 10 个策略",
        )

    try:
        results = await batch_backtest(
            db=db,
            strategy_ids=req.strategy_ids,
            stock_code=req.stock_code,
            start_date=req.start_date,
            end_date=req.end_date,
            timeframe=req.timeframe,
        )
    except StrategyNotFound as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    except StrategyNotActive as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    except InvalidStrategyError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    return {
        "success": True,
        "data": {
            "results": [
                {
                    "session_id": r.session_id,
                    "strategy_id": r.strategy_id,
                    "strategy_name": r.strategy_name,
                    "total_bars": r.total_bars,
                    "time_elapsed": r.time_elapsed,
                }
                for r in results
            ]
        },
        "message": "并发回测完成",
    }


# ============================================================
# P3: 策略版本对比
# ============================================================

@router.post(
    "/compare",
    response_model=dict,
    status_code=200,
    tags=["策略引擎"],
    summary="对比两个版本回测结果",
)
async def compare_versions_endpoint(
    req: CompareRequest,
    db: AsyncSession = Depends(get_db),
):
    """对比两个策略版本的回测结果。"""
    from .exceptions import InvalidStrategyError, StrategyNotFound
    from .runtime.compare import CompareService

    if len(req.version_ids) != 2:
        raise HTTPException(
            status_code=400,
            detail="版本 ID 列表必须包含 2 个版本",
        )

    svc = CompareService()
    try:
        result = await svc.compare(
            db=db,
            version_ids=req.version_ids,
            stock_code=req.stock_code,
            start_date=req.start_date,
            end_date=req.end_date,
            timeframe=req.timeframe,
        )
    except InvalidStrategyError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    except StrategyNotFound as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    return {
        "success": True,
        "data": {
            "version_1": result.version_1,
            "version_2": result.version_2,
            "diff": result.diff,
        },
        "message": "版本对比完成",
    }


# ============================================================
# P3: 策略导出/导入
# ============================================================

@router.post(
    "/export",
    response_model=dict,
    status_code=200,
    tags=["策略引擎"],
    summary="导出策略为 JSON",
)
async def export_strategies_endpoint(
    req: ExportRequest,
    db: AsyncSession = Depends(get_db),
):
    """导出策略为 JSON 格式。"""
    from .runtime.sharing import SharingService

    svc = SharingService()
    try:
        items = await svc.export_strategies(
            db=db,
            strategy_ids=req.strategy_ids,
            include_versions=req.include_versions,
        )
    except StrategyNotFound as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    return {
        "success": True,
        "data": {
            "strategies": [
                {
                    "code": item.code,
                    "name": item.name,
                    "strategy_type": item.strategy_type,
                    "description": item.description,
                    "code_content": item.code_content,
                    "parameters": item.parameters,
                    "versions": item.versions if req.include_versions else None,
                }
                for item in items
            ],
        },
        "message": "策略导出成功",
    }


@router.post(
    "/import",
    response_model=dict,
    status_code=200,
    tags=["策略引擎"],
    summary="导入 JSON 策略",
)
async def import_strategies_endpoint(
    req: ImportRequest,
    db: AsyncSession = Depends(get_db),
):
    """导入 JSON 格式的策略，跳过重复策略。"""
    from .runtime.sharing import SharingService

    svc = SharingService()
    strategies_data = [item.model_dump() for item in req.strategies]
    result = await svc.import_strategies(db=db, strategies_data=strategies_data)

    return {
        "success": True,
        "data": {
            "imported": result.imported,
            "skipped": result.skipped,
            "skipped_codes": result.skipped_codes,
        },
        "message": f"导入完成：成功 {result.imported} 个，跳过 {result.skipped} 个",
    }




__all__ = ["router"]
