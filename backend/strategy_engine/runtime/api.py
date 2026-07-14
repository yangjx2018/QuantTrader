"""DSL 内置 API：注入策略代码的全局函数。

策略代码在 handle_data 中调用这些函数下单/读历史/打日志。
所有函数依赖运行时上下文（StrategyContext + DataLoader），通过闭包绑定。

设计原则：
- order_* 函数不立即成交，仅向 context.pending_orders 追加订单
- 引擎在 handle_data 调用结束后批量撮合（按当 bar 收盘价）
- get_history 通过 data_loader 回调获取数据，引擎负责传入实现
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Optional

from strategy_engine.runtime.context import StrategyContext


# ============================================================
# 日志
# ============================================================

@dataclass
class DSLLogger:
    """策略代码使用的 log 对象。

    日志由引擎在每 bar 收集到 BarRecord.log_entries 中，
    前缀为 [INFO] / [WARN] / [ERROR]。
    """

    sink: list[str]  # 引擎提供的共享列表（每 bar 重置）

    def info(self, msg: Any) -> None:
        self.sink.append(f"[INFO] {msg}")

    def warn(self, msg: Any) -> None:
        self.sink.append(f"[WARN] {msg}")

    def warning(self, msg: Any) -> None:
        # alias
        self.warn(msg)

    def error(self, msg: Any) -> None:
        self.sink.append(f"[ERROR] {msg}")


# ============================================================
# 订单（pending_orders 的元素结构）
# ============================================================

@dataclass
class PendingOrder:
    """待撮合订单（策略通过 order_* API 写入）。"""

    security: str
    side: str          # "buy" / "sell"
    mode: str          # "quantity" / "value" / "target_quantity" / "target_value"
    target: float      # 视 mode 语义而定：股数 / 金额 / 目标股数 / 目标金额


# ============================================================
# 下单 API 工厂
# ============================================================

def _round_to_lot(qty: float) -> int:
    """A 股最小委托单位：100 股整数倍，向下取整。"""
    return int(qty // 100) * 100


def make_order_api(context: StrategyContext, log: DSLLogger):
    """构造 order/order_value/order_target/order_target_value 函数族。

    Args:
        context: 当前策略上下文（订单写入 context.pending_orders）
        log: 策略日志对象

    Returns:
        dict 含 order/order_value/order_target/order_target_value 四个函数，
        可直接注入策略 globals。
    """

    def order(security: str, amount: int) -> None:
        """按股数下单。

        amount > 0 买入；amount < 0 卖出（卖出绝对值股数）。
        amount 必须为 100 的整数倍；非整数倍向下取整。
        """
        if amount == 0:
            log.warn(f"order 数量为 0，跳过: {security}")
            return
        side = "buy" if amount > 0 else "sell"
        qty = abs(amount)
        if side == "buy":
            qty = _round_to_lot(qty)
            if qty == 0:
                log.warn(f"order 买入数量不足 100 股，跳过: {security} amount={amount}")
                return
        # 卖出允许任意数量（部分卖出时可能不是 100 倍，引擎撮合时校验持仓）
        context.pending_orders.append(PendingOrder(
            security=security,
            side=side,
            mode="quantity",
            target=float(qty),
        ).__dict__)

    def order_value(security: str, value: float) -> None:
        """按金额下单。

        value > 0 买入；value < 0 卖出。
        买入：根据 current_price 算可买股数（向下取整到 100 倍）。
        卖出：按当前持仓比例卖出（取近似股数）。
        """
        if value == 0:
            log.warn(f"order_value 金额为 0，跳过: {security}")
            return
        side = "buy" if value > 0 else "sell"
        context.pending_orders.append(PendingOrder(
            security=security,
            side=side,
            mode="value",
            target=abs(float(value)),
        ).__dict__)

    def order_target(security: str, target_qty: int) -> None:
        """调仓到目标股数。引擎根据当前持仓算差额。"""
        if target_qty < 0:
            log.warn(f"order_target 目标股数不能为负，跳过: {security} target={target_qty}")
            return
        context.pending_orders.append(PendingOrder(
            security=security,
            side="buy",  # 引擎根据差额决定买卖方向
            mode="target_quantity",
            target=float(target_qty),
        ).__dict__)

    def order_target_value(security: str, target_value: float) -> None:
        """调仓到目标市值。"""
        if target_value < 0:
            log.warn(f"order_target_value 目标市值不能为负，跳过: {security} target={target_value}")
            return
        context.pending_orders.append(PendingOrder(
            security=security,
            side="buy",
            mode="target_value",
            target=float(target_value),
        ).__dict__)

    return {
        "order": order,
        "order_value": order_value,
        "order_target": order_target,
        "order_target_value": order_target_value,
    }


# ============================================================
# get_history API 工厂
# ============================================================

# data_loader 签名：(n, unit, fields, security, fq, include, context) -> dict[str, list]
# 引擎负责提供实现，封装 K 线查询逻辑
HistoryLoader = Callable[..., dict[str, list]]


def make_get_history(loader: HistoryLoader, context: StrategyContext):
    """构造 get_history 函数。

    策略代码：
        df = get_history(20, '1d', 'close', '000001.SZ', fq='qfq', include=False)
        # 返回 dict-like：df['close'] = [c1, c2, ..., c20]
    """

    def get_history(
        n: int,
        unit: str = "1d",
        fields: str | list[str] = "close",
        security: Optional[str] = None,
        fq: Optional[str] = "qfq",
        include: bool = False,
    ) -> dict[str, list]:
        if security is None:
            # 默认取 context.universe[0]
            if not context.universe:
                return {}
            security = context.universe[0]
        return loader(
            n=n,
            unit=unit,
            fields=fields,
            security=security,
            fq=fq,
            include=include,
            context=context,
        )

    return get_history


# ============================================================
# set_universe / get_current_data
# ============================================================

def make_set_universe(context: StrategyContext):
    def set_universe(security_list) -> None:
        if isinstance(security_list, str):
            security_list = [security_list]
        context.universe = list(security_list)
    return set_universe


def make_get_current_data(context: StrategyContext):
    def get_current_data() -> dict:
        """返回当前 bar 的快照（P1 简化：仅含当前价）。"""
        return {
            sec: {"close": pos.current_price}
            for sec, pos in context.portfolio.positions.items()
        }
    return get_current_data


__all__ = [
    "DSLLogger",
    "PendingOrder",
    "make_order_api",
    "make_get_history",
    "make_set_universe",
    "make_get_current_data",
]
