"""策略版本对比服务。

提供两个策略版本的回测结果对比（收益率/夏普/最大回撤/胜率/盈亏比）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from strategy_engine.exceptions import (
    InvalidStrategyError,
    StrategyNotActive,
    StrategyNotFound,
)
from strategy_engine.repository import StrategyVersionRepository
from strategy_engine.runtime.types import BacktestResult

logger = logging.getLogger(__name__)


@dataclass
class CompareDiff:
    """两个版本回测结果的差异。"""
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_loss_ratio: float


@dataclass
class CompareResult:
    """版本对比完整结果。"""
    version_1: dict
    version_2: dict
    diff: dict


class CompareService:
    """策略版本对比服务。

    用法：
        svc = CompareService()
        result = await svc.compare(db, version_ids, stock_code, start_date, end_date, timeframe)
    """

    async def compare(
        self,
        db: AsyncSession,
        version_ids: list[int],
        stock_code: str,
        start_date: str,
        end_date: str,
        timeframe: str = "1d",
    ) -> CompareResult:
        """对比两个版本的回测结果。

        Args:
            db: 数据库会话
            version_ids: 两个版本 ID（必须 2 个）
            stock_code: 股票代码
            start_date: 起始日期
            end_date: 结束日期
            timeframe: 时间框架

        Returns:
            CompareResult: 含 version_1、version_2 和 diff

        Raises:
            InvalidStrategyError: version_ids 长度 != 2
            StrategyNotFound: 版本不存在
        """
        if len(version_ids) != 2:
            raise InvalidStrategyError("版本 ID 列表必须包含 2 个版本")

        version_repo = StrategyVersionRepository(db)

        # 加载两个版本
        v1 = await version_repo.get_by_id(version_ids[0])
        if not v1:
            raise StrategyNotFound(f"版本 {version_ids[0]} 不存在")

        v2 = await version_repo.get_by_id(version_ids[1])
        if not v2:
            raise StrategyNotFound(f"版本 {version_ids[1]} 不存在")

        # 使用 strategy_engine 的回测引擎分别回测
        from strategy_engine.service import run_backtest

        result_1: BacktestResult = await run_backtest(
            db=db,
            stock_code=stock_code,
            strategy_id=v1.strategy_id,
            account_id=0,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
        )

        result_2: BacktestResult = await run_backtest(
            db=db,
            stock_code=stock_code,
            strategy_id=v2.strategy_id,
            account_id=0,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
        )

        # 计算指标
        metrics_1 = self._calc_metrics(result_1)
        metrics_2 = self._calc_metrics(result_2)

        # 计算差异
        diff = CompareDiff(
            total_return_pct=round(metrics_2["total_return_pct"] - metrics_1["total_return_pct"], 2),
            sharpe_ratio=round(metrics_2["sharpe_ratio"] - metrics_1["sharpe_ratio"], 4),
            max_drawdown_pct=round(metrics_2["max_drawdown_pct"] - metrics_1["max_drawdown_pct"], 2),
            win_rate_pct=round(metrics_2["win_rate_pct"] - metrics_1["win_rate_pct"], 2),
            profit_loss_ratio=round(metrics_2["profit_loss_ratio"] - metrics_1["profit_loss_ratio"], 4),
        )

        return CompareResult(
            version_1={
                "version_id": version_ids[0],
                "version": v1.version,
                "metrics": metrics_1,
            },
            version_2={
                "version_id": version_ids[1],
                "version": v2.version,
                "metrics": metrics_2,
            },
            diff={
                "total_return_pct": diff.total_return_pct,
                "sharpe_ratio": diff.sharpe_ratio,
                "max_drawdown_pct": diff.max_drawdown_pct,
                "win_rate_pct": diff.win_rate_pct,
                "profit_loss_ratio": diff.profit_loss_ratio,
            },
        )

    def _calc_metrics(self, result: BacktestResult) -> dict:
        """从 BacktestResult 计算核心指标。"""
        if not result.bars or result.total_bars == 0:
            return {
                "total_return_pct": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "win_rate_pct": 0.0,
                "profit_loss_ratio": 0.0,
            }

        # 收益率
        initial = result.bars[0].total_assets
        final = result.bars[-1].total_assets
        total_return_pct = round((final / initial - 1.0) * 100, 2) if initial > 0 else 0.0

        # 最大回撤
        peak = result.bars[0].total_assets
        max_dd = 0.0
        for bar in result.bars:
            if bar.total_assets > peak:
                peak = bar.total_assets
            dd = (bar.total_assets / peak - 1.0) * 100 if peak > 0 else 0.0
            if dd < max_dd:
                max_dd = dd
        max_drawdown_pct = round(max_dd, 2)

        # 收集所有订单
        all_orders = []
        for bar in result.bars:
            for order in bar.orders:
                all_orders.append(order)

        # 胜率 & 盈亏比
        win_trades = [o for o in all_orders if o.pnl > 0]
        loss_trades = [o for o in all_orders if o.pnl < 0]
        win_rate_pct = round(len(win_trades) / len(all_orders) * 100, 2) if all_orders else 0.0

        avg_win = sum(o.pnl for o in win_trades) / len(win_trades) if win_trades else 0.0
        avg_loss = abs(sum(o.pnl for o in loss_trades) / len(loss_trades)) if loss_trades else 1.0
        profit_loss_ratio = round(avg_win / avg_loss, 4) if avg_loss > 0 else 0.0

        # 夏普比率（简化版：基于每日收益率）
        sharpe_ratio = self._calc_sharpe(result)

        return {
            "total_return_pct": total_return_pct,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown_pct": max_drawdown_pct,
            "win_rate_pct": win_rate_pct,
            "profit_loss_ratio": profit_loss_ratio,
        }

    def _calc_sharpe(self, result: BacktestResult) -> float:
        """计算年化夏普比率（简化版）。"""
        if len(result.bars) < 2:
            return 0.0

        # 计算每日收益率
        returns = []
        for i in range(1, len(result.bars)):
            prev = result.bars[i - 1].total_assets
            curr = result.bars[i].total_assets
            if prev > 0:
                returns.append((curr / prev) - 1.0)

        if not returns:
            return 0.0

        # 均值 / 标准差 * sqrt(252)
        import math
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        std_ret = math.sqrt(variance) if variance > 0 else 0.0

        if std_ret == 0:
            return 0.0

        return round(mean_ret / std_ret * math.sqrt(252), 4)


__all__ = ["CompareService", "CompareDiff", "CompareResult"]
