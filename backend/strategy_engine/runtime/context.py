"""策略上下文数据结构。

定义策略代码通过 `context` 参数能访问到的对象：
- context.portfolio: Portfolio，账户持仓与现金
- context.current_dt: 当前 bar 日期字符串
- context.current_price: 当前 bar 收盘价
- context.universe: set_universe 设置的标的池

设计原则：策略代码不应直接修改 Portfolio 字段，必须通过 order_* API 下单；
引擎在每个 bar 处理前更新 context 快照。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Position:
    """单标的持仓快照。

    业务含义：某时刻账户中某只股票的持仓状态。
    用途：策略通过 context.portfolio.positions[security] 读取，
          策略代码 MUST NOT 直接修改本对象，必须通过 order_* API。
    """

    security: str
    quantity: int = 0          # 持仓股数（A 股最小 100 股整数倍）
    cost_price: float = 0.0    # 平均成本价
    current_price: float = 0.0  # 当前价（由引擎每 bar 更新）

    @property
    def market_value(self) -> float:
        """市值 = quantity × current_price。"""
        return round(self.quantity * self.current_price, 2)

    @property
    def floating_pnl(self) -> float:
        """浮盈 = (current_price - cost_price) × quantity。正数浮盈，负数浮亏。"""
        return round((self.current_price - self.cost_price) * self.quantity, 2)


@dataclass
class Portfolio:
    """账户组合状态。

    业务含义：当前账户的现金 + 所有持仓的总和。
    由引擎维护，每个 bar 撮合后更新。
    """

    initial_capital: float = 0.0
    cash: float = 0.0
    commission_rate: float = 0.001  # 默认 0.1%
    positions: dict[str, Position] = field(default_factory=dict)

    @property
    def total_value(self) -> float:
        """总资产 = cash + sum(positions.market_value)。"""
        positions_value = sum(p.market_value for p in self.positions.values())
        return round(self.cash + positions_value, 2)

    def get_position(self, security: str) -> Optional[Position]:
        """读取指定标的持仓，无则返回 None。"""
        return self.positions.get(security)

    def update_current_price(self, security: str, price: float) -> None:
        """更新某标的的当前价（引擎每 bar 调用）。"""
        pos = self.positions.get(security)
        if pos is not None:
            pos.current_price = price

    # === 以下方法仅供引擎内部使用，策略代码不应调用 ===

    def _apply_buy(
        self,
        security: str,
        quantity: int,
        price: float,
        commission: float,
    ) -> None:
        """执行买入：扣现金、新增/合并持仓。"""
        amount = quantity * price
        if self.cash < amount + commission:
            raise ValueError("cash insufficient")
        self.cash = round(self.cash - amount - commission, 2)

        existing = self.positions.get(security)
        if existing is None:
            self.positions[security] = Position(
                security=security,
                quantity=quantity,
                cost_price=price,
                current_price=price,
            )
        else:
            new_qty = existing.quantity + quantity
            # 加权平均成本
            new_cost = (
                (existing.cost_price * existing.quantity + price * quantity)
                / new_qty
            )
            existing.quantity = new_qty
            existing.cost_price = round(new_cost, 4)
            existing.current_price = price

    def _apply_sell(
        self,
        security: str,
        quantity: int,
        price: float,
        commission: float,
    ) -> float:
        """执行卖出：加现金、减持仓，返回该笔已实现 pnl。"""
        pos = self.positions.get(security)
        if pos is None or pos.quantity < quantity:
            raise ValueError("position insufficient")

        amount = quantity * price
        realized_pnl = round(
            (price - pos.cost_price) * quantity - commission,
            2,
        )

        self.cash = round(self.cash + amount - commission, 2)
        pos.quantity -= quantity
        pos.current_price = price
        if pos.quantity == 0:
            # 全部卖出后保留 0 持仓记录（便于历史追溯），或可选 pop
            self.positions.pop(security, None)
        return realized_pnl


@dataclass
class StrategyContext:
    """策略钩子执行时传入的上下文。

    每个回测会话一份；引擎在每个 bar 处理前更新 current_dt/current_price。
    策略代码访问：
        context.portfolio.cash
        context.portfolio.positions
        context.portfolio.total_value
        context.current_dt
        context.current_price
    """

    portfolio: Portfolio
    current_dt: Optional[str] = None       # YYYY-MM-DD
    current_price: Optional[float] = None
    universe: list[str] = field(default_factory=list)
    # 内部：策略代码通过 order_* API 写入的待撮合订单
    pending_orders: list[dict] = field(default_factory=list)

    def reset_pending_orders(self) -> None:
        """引擎在每 bar 开始时调用，清空上一轮的待撮合订单。"""
        self.pending_orders.clear()
