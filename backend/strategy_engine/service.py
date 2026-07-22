"""strategy_engine 业务编排层。

提供两个入口：
- run_backtest(stock_code, strategy_id, account_id, timeframe, start_date, end_date)
    真实回测。P1 阶段 fetchers 仍用 MockKLineFetcher（V 形趋势 60 bar），
    但引擎、撮合、策略加载都走真实路径；后续接入 api_data 时只需替换 fetcher。
- dry_run(strategy_id, stock_code, start_date, end_date, max_bars, parameters)
    试运行，同样基于 mock 数据，让用户快速验证策略代码

dry-run 与真实回测共用 BacktestEngine，区别：
- run_backtest 接受完整的 6 元组签名（history_replay 期望），不限制 max_bars
- dry_run 限 max_bars，允许传入临时参数覆盖
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from strategy_engine.exceptions import (
    InvalidStrategyError,
    StrategyNotActive,
    StrategyNotFound,
)
from strategy_engine.models import Strategy
from strategy_engine.repository import StrategyRepository
from strategy_engine.runtime.engine import BacktestEngine
from strategy_engine.runtime.mock_data import (
    MockAccountFetcher,
    MockBenchmarkFetcher,
    MockKLineFetcher,
)
from strategy_engine.runtime.real_data import (
    RealAccountFetcher,
    RealKLineFetcher,
)
from strategy_engine.runtime.types import BacktestResult
from strategy_engine.schemas import DryRunResponse


# ============================================================
# 真实回测入口（P1 阶段使用 mock fetchers，待 api_data 接入后替换）
# ============================================================

async def run_backtest(
    db: AsyncSession,
    stock_code: str,
    strategy_id: int,
    account_id: int,
    timeframe: str,
    start_date: str,
    end_date: str,
) -> BacktestResult:
    """真实回测入口。

    签名与 history_replay/strategy_mock.py:run_backtest_mock 对齐，
    便于跨模块替换。

    P1 阶段 fetchers 仍用 mock（不依赖 api_data 真实数据），
    但策略加载 / 引擎 / 撮合 / 历史 buffer 全走真实路径。

    Args:
        db: 异步 DB 会话（用于查 strategy）
        stock_code: 股票代码（P1 mock 数据忽略此字段）
        strategy_id: 策略 ID
        account_id: 账户 ID（P1 mock 数据忽略此字段）
        timeframe: K 线周期（P1 仅支持 "1d"）
        start_date: 起始日期（P1 mock 数据忽略）
        end_date: 结束日期（P1 mock 数据忽略）

    Returns:
        BacktestResult：完整的回测结果

    Raises:
        StrategyNotFound: strategy_id 不存在
        StrategyNotActive: status != 'active'
        InvalidStrategyError: code_content 为空
        BacktestError: 其他引擎错误（如 timeframe 不支持）
    """
    # 1. 查 strategy
    repo = StrategyRepository(db)
    strategy: Optional[Strategy] = await repo.get_by_id(strategy_id)
    if not strategy:
        raise StrategyNotFound(f"策略 {strategy_id} 不存在")
    if strategy.status != "active":
        raise StrategyNotActive(
            f"策略 {strategy_id} 状态非 active，当前 status={strategy.status}"
        )
    if not strategy.code_content:
        raise InvalidStrategyError("策略代码为空，无法回测")

    # 2. 构造 fetchers（根据环境变量选择 mock 或 real）
    use_real_data = os.environ.get("USE_REAL_DATA", "false").lower() in ("true", "1", "yes")

    if use_real_data:
        # P2: 使用真实 api_data 和 account_trading fetchers
        kline_fetcher = RealKLineFetcher(db)
        account_fetcher = RealAccountFetcher(db)
    else:
        # P1: 使用 mock fetchers（不依赖外部服务）
        kline_fetcher = MockKLineFetcher(max_bars=250)  # 给足 250 bar 覆盖常规年度回测
        account_fetcher = MockAccountFetcher()

    engine = BacktestEngine(
        kline_fetcher=kline_fetcher,
        benchmark_fetcher=MockBenchmarkFetcher(),  # TODO: 后续可替换为 RealBenchmarkFetcher
        account_fetcher=account_fetcher,
    )

    # 3. 跑回测
    return await engine.run(
        strategy=strategy,
        stock_code=stock_code,
        account_id=account_id,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        session_id=f"backtest_{strategy_id}_{stock_code}",
    )


# ============================================================
# dry-run 入口
# ============================================================

async def dry_run(
    db: AsyncSession,
    strategy_id: int,
    stock_code: str,
    start_date: str,
    end_date: str,
    max_bars: int = 30,
    parameters: Optional[dict] = None,
) -> DryRunResponse:
    """基于 mock 数据的试运行。

    业务流程：
      1. 查 DB 拿策略（不存在 → 404；status != active → 400）
      2. 用 mock fetchers 构造 BacktestEngine
      3. 调 engine.run，限定 max_bars 个 bar
      4. 把 BacktestResult 转为 DryRunResponse（简化字段）

    Args:
        db: 异步 DB 会话
        strategy_id: 策略 ID
        stock_code: 股票代码（P1 用于 universe 标识，不实际拉数据）
        start_date: 起始日期（仅展示，mock 数据忽略）
        end_date: 结束日期（仅展示，mock 数据忽略）
        max_bars: 取多少根 bar 跑（1~100）
        parameters: 覆盖策略默认参数；None 用策略自带

    Returns:
        DryRunResponse：含 session_id / total_bars / final_capital /
        total_return_pct / bars 简化列表

    Raises:
        StrategyNotFound: strategy_id 不存在
        StrategyNotActive: status != 'active'
        InvalidStrategyError: code_content 为空
    """
    # 1. 查 DB
    repo = StrategyRepository(db)
    strategy: Optional[Strategy] = await repo.get_by_id(strategy_id)
    if not strategy:
        raise StrategyNotFound(f"策略 {strategy_id} 不存在")
    if strategy.status != "active":
        raise StrategyNotActive(
            f"策略 {strategy_id} 状态非 active，当前 status={strategy.status}"
        )
    if not strategy.code_content:
        raise InvalidStrategyError("策略代码为空，无法试运行")

    # 2. 用 mock fetchers 构造引擎
    engine = BacktestEngine(
        kline_fetcher=MockKLineFetcher(max_bars=max_bars),
        benchmark_fetcher=MockBenchmarkFetcher(),
        account_fetcher=MockAccountFetcher(),
    )

    # 3. 跑回测（参数覆盖）
    if parameters:
        # 合并参数：策略默认参数 + 用户覆盖
        merged = dict(strategy.parameters or {})
        merged.update(parameters)
        # 构造临时 strategy-like 对象（避免修改 ORM 实例）
        class _Tmp:
            pass
        tmp = _Tmp()
        tmp.id = strategy.id
        tmp.name = strategy.name
        tmp.status = strategy.status
        tmp.code_content = strategy.code_content
        tmp.parameters = merged
        result_strategy = tmp
    else:
        result_strategy = strategy

    # BacktestEngine.run 内部会校验 strategy 状态与 code_content，
    # 但我们已经在上层校验过；传入 strategy_obj 即可
    result: BacktestResult = await engine.run(
        strategy=result_strategy,
        stock_code=stock_code,
        account_id=0,  # mock 账户
        timeframe="1d",
        start_date=start_date,
        end_date=end_date,
        session_id=f"dryrun_{strategy_id}_{stock_code}",
    )

    # 4. 转为 DryRunResponse
    return _to_dry_run_response(result)


# ============================================================
# 内部：BacktestResult → DryRunResponse
# ============================================================

def _to_dry_run_response(result: BacktestResult) -> DryRunResponse:
    """把完整 BacktestResult 转为简化的 DryRunResponse。"""
    from strategy_engine.schemas import DryRunBarSummary

    initial = result.bars[0].total_assets if result.bars else 0.0
    final = result.bars[-1].total_assets if result.bars else 0.0
    if initial > 0:
        return_pct = round((final / initial - 1.0) * 100, 2)
    else:
        return_pct = 0.0

    bars_summary = [
        DryRunBarSummary(
            time=b.time,
            close=b.close,
            total_assets=b.total_assets,
            signal=b.signal,
            orders_count=len(b.orders),
        )
        for b in result.bars
    ]

    return DryRunResponse(
        session_id=result.session_id,
        total_bars=result.total_bars,
        time_elapsed=result.time_elapsed,
        final_capital=final,
        total_return_pct=return_pct,
        bars=bars_summary,
    )


# ============================================================
# 实盘对接：load_strategy 接口
# ============================================================

async def load_strategy(
    db: AsyncSession,
    strategy_id: int,
) -> "StrategyInstance":
    """加载策略实例（优先从缓存获取，否则从 DB 加载并缓存）。

    供 strategy_execution 模块调用，用于实盘交易。

    Args:
        db: 数据库会话
        strategy_id: 策略 ID

    Returns:
        StrategyInstance: 加载好的策略实例

    Raises:
        StrategyNotFound: 策略不存在
        StrategyNotActive: 策略未启用
        InvalidStrategyError: 策略代码无效
    """
    from strategy_engine.runtime.registry import get_global_registry
    from strategy_engine.runtime.loader import StrategyLoader, StrategyInstance

    registry = get_global_registry()

    # 定义 loader_func：从 DB 加载策略并编译
    async def loader_func(sid: int) -> StrategyInstance:
        repo = StrategyRepository(db)
        strategy = await repo.get_by_id(sid)

        if not strategy:
            raise StrategyNotFound(f"策略 {sid} 不存在")

        if strategy.status != "active":
            raise StrategyNotActive(f"策略 {sid} 未启用（status={strategy.status}）")

        if not strategy.code_content:
            raise InvalidStrategyError(f"策略 {sid} 代码为空")

        # 使用 StrategyLoader 编译策略代码
        loader = StrategyLoader()
        instance = loader.load(
            code_content=strategy.code_content,
            parameters=strategy.parameters or {},
        )

        return instance

    # 从缓存获取（未命中则调用 loader_func）
    instance = await registry.get(strategy_id, loader_func)

    if instance is None:
        raise StrategyNotFound(f"策略 {strategy_id} 加载失败")

    return instance


# ============================================================
# P3: 并发回测
# ============================================================

async def batch_backtest(
    db: AsyncSession,
    strategy_ids: list[int],
    stock_code: str,
    start_date: str,
    end_date: str,
    timeframe: str = "1d",
) -> list[BacktestResult]:
    """并发执行多个策略回测。

    Args:
        db: 数据库会话
        strategy_ids: 策略 ID 列表（最多 10 个）
        stock_code: 股票代码
        start_date: 起始日期
        end_date: 结束日期
        timeframe: 时间框架

    Returns:
        list[BacktestResult]: 各策略的回测结果

    Raises:
        InvalidStrategyError: strategy_ids 长度 > 10
    """
    if len(strategy_ids) > 10:
        raise InvalidStrategyError("并发回测最多支持 10 个策略")

    import asyncio

    async def _run_one(sid: int) -> BacktestResult:
        return await run_backtest(
            db=db,
            stock_code=stock_code,
            strategy_id=sid,
            account_id=0,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
        )

    tasks = [_run_one(sid) for sid in strategy_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 将异常转换为结果中的错误标记
    output: list[BacktestResult] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.error(f"Backtest failed for strategy {strategy_ids[i]}: {r}")
            # 返回一个空结果标记错误
            from strategy_engine.runtime.types import BacktestResult as BR
            output.append(BR(
                session_id=f"error_{strategy_ids[i]}",
                stock_code=stock_code,
                strategy_id=strategy_ids[i],
                strategy_name=f"error: {r}",
                account_id=0,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                total_bars=0,
                time_elapsed=0.0,
                bars=[],
            ))
        else:
            output.append(r)

    return output


__all__ = ["batch_backtest", "dry_run", "load_strategy", "run_backtest"]
