"""
模拟策略模块的回测方法返回数据。

本文件承担两个职责：
1. **类型契约 re-export**：BacktestResult / BarRecord / OrderRecord / PositionRecord
   单源在 strategy_engine.runtime.types，本文件 re-export 便于向后兼容。
2. **run_backtest_mock**：保留原有纯 Python 模拟实现，作为：
   - 开发时不需要 DB / 不需要 strategy_engine 模块时的独立测试兜底
   - 联调阶段设置 USE_MOCK_STRATEGY=true 环境变量可强制走本实现

调用方式：
    from strategy_engine.service import run_backtest
    result = await run_backtest(db, stock_code, strategy_id, account_id, timeframe, start_date, end_date)

    # 仅在需要独立 mock 时使用：
    from history_replay.strategy_mock import run_backtest_mock
    result = await run_backtest_mock(stock_code, strategy_id, account_id, timeframe, start_date, end_date)

    # 强制走 mock：
    # export USE_MOCK_STRATEGY=true
"""

import os
import random
from datetime import date, timedelta
from typing import Optional

# === 类型契约 re-export（向后兼容，单源在 strategy_engine.runtime.types） ===
from strategy_engine.runtime.types import (  # noqa: F401
    BacktestResult,
    BarRecord,
    OrderRecord,
    PositionRecord,
)


# ============================================================
# Mock 数据生成（保留原有实现，仅 USE_MOCK_STRATEGY=true 时使用）
# ============================================================

def _generate_trading_days(start: str, end: str) -> list[str]:
    """
    生成交易日期列表（跳过周末）。
    
    业务含义：A股市场只在周一到周五交易，回测只覆盖交易日。
    这是一个简化版，真实场景需考虑节假日。
    """
    start_dt = date.fromisoformat(start)
    end_dt = date.fromisoformat(end)
    days = []
    current = start_dt
    while current <= end_dt:
        # 0=Monday, 4=Friday, 跳过周末
        if current.weekday() <= 4:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _generate_price_series(
    trading_days: list[str],
    start_price: float = 11.00,
) -> list[dict]:
    """
    生成模拟K线价格序列。
    
    业务含义：模拟000001.SZ平安银行2024年全年日线走势。
    通过预定义的关键价格节点插值生成，确保走势有趋势和波动，
    而不是纯随机——这样回测结果才有意义。
    
    价格走势设计：
    11.00(年初) → 9.80(2月底部) → 11.10(3月反弹) → 11.50(4月)
    → 10.80(6月回调) → 12.80(8月高点) → 11.40(9月回调) → 13.00(11月) → 12.60(年末)
    """
    # 关键价格节点：(月份索引, 目标价格)
    # 月份从0开始，0=1月，1=2月...
    key_points = [
        (0, 11.00),   # 年初
        (1.8, 9.80),  # 2月底部
        (2.8, 11.10), # 3月反弹
        (3.5, 11.50), # 4月
        (5.5, 10.80), # 6月回调
        (7.5, 12.80), # 8月高点
        (8.5, 11.40), # 9月回调
        (10.5, 13.00),# 11月新高
        (11.5, 12.60),# 年末
    ]

    total_days = len(trading_days)
    bars = []
    random.seed(42)

    for i, day in enumerate(trading_days):
        # 计算当前月份位置（0~11.99）
        month_pos = i / total_days * 12

        # 在关键节点之间线性插值得到基准价格
        base_price = start_price
        for j in range(len(key_points) - 1):
            m1, p1 = key_points[j]
            m2, p2 = key_points[j + 1]
            if m1 <= month_pos < m2:
                ratio = (month_pos - m1) / (m2 - m1)
                base_price = p1 + (p2 - p1) * ratio
                break
        else:
            base_price = key_points[-1][1]

        # 添加小幅随机波动，让K线更真实
        noise = random.gauss(0, 0.05)
        close = round(base_price + noise, 2)

        # 生成OHLC：基于收盘价上下波动
        change = random.gauss(0, 0.08)
        open_price = round(close - change, 2)
        high = round(max(open_price, close) + abs(random.gauss(0, 0.06)), 2)
        low = round(min(open_price, close) - abs(random.gauss(0, 0.06)), 2)
        volume = int(random.gauss(50000000, 10000000))
        amount = round(close * volume, 2)

        bars.append({
            "time": day,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": max(volume, 0),
            "amount": amount,
            "base_price": base_price,
        })

    return bars


def _generate_benchmark_series(
    trading_days: list[str],
    start_price: float = 3300.0,
) -> dict[str, float]:
    """
    生成基准指数（如沪深300）的收盘价序列。
    
    业务含义：模拟市场基准走势，用于和策略收益对比。
    基准走势比个股平缓，波动更小。
    
    用途：
    - 计算基准收益率
    - 计算Alpha、Beta、信息率
    - 策略vs基准收益对比图
    """
    random.seed(88)
    benchmark = {}
    price = start_price

    for day in trading_days:
        # 日涨幅在 -1.5% ~ +1.5% 之间，波动比个股小
        daily_return = random.gauss(0.0002, 0.008)
        price = round(price * (1 + daily_return), 2)
        benchmark[day] = price

    return benchmark


async def run_backtest_mock(
    stock_code: str,
    strategy_id: int,
    account_id: int,
    timeframe: str,
    start_date: str,
    end_date: str,
    db=None,  # Optional[AsyncSession]：传入则走真实引擎，否则走纯 mock
) -> BacktestResult:
    """
    策略回测入口（历史回放模块专用）。

    路由逻辑：
      - 默认行为：调用 strategy_engine.service.run_backtest（真实引擎 + 策略库）
      - env USE_MOCK_STRATEGY=true：强制走本文件原有纯 Python 模拟
      - 无 db：回退到纯 Python 模拟（用于单元测试）

    参数：
        stock_code:   股票代码（如 "000001.SZ"）
        strategy_id:  策略 ID（对应 strategy 表主键）
        account_id:   账户 ID
        timeframe:    K 线周期（如 "1d"）
        start_date:   起始日期（YYYY-MM-DD）
        end_date:     结束日期（YYYY-MM-DD）
        db:           可选 AsyncSession；不传则走 mock 路径
    """
    # 1. 检查 USE_MOCK_STRATEGY 环境变量
    use_mock = os.environ.get("USE_MOCK_STRATEGY", "").lower() in ("1", "true", "yes")

    # 2. 默认走真实引擎（需要 db）
    if not use_mock and db is not None:
        from strategy_engine.service import run_backtest as real_run_backtest
        return await real_run_backtest(
            db=db,
            stock_code=stock_code,
            strategy_id=strategy_id,
            account_id=account_id,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
        )

    # 3. 回退到原有纯 Python 模拟（无 db 或强制 mock 时）
    # 生成交易日历
    trading_days = _generate_trading_days(start_date, end_date)
    if not trading_days:
        return BacktestResult(
            session_id="mock_0",
            stock_code=stock_code,
            strategy_id=strategy_id,
            strategy_name="模拟策略",
            account_id=account_id,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            total_bars=0,
            time_elapsed=0.0,
            bars=[],
        )

    # 生成K线行情
    kline_bars = _generate_price_series(trading_days)

    # 生成基准价格
    benchmark_prices = _generate_benchmark_series(trading_days)

    # 策略名称映射（模拟策略模块的策略配置）
    strategy_names = {
        1: "双均线交叉",
        2: "RSI超买超卖",
        3: "布林带突破",
        4: "MACD金叉死叉",
    }

    # 虚拟账户配置（模拟账户模块的账户信息）
    account_configs = {
        1: {"initial_capital": 1000000.0, "commission_rate": 0.001},
        2: {"initial_capital": 500000.0,  "commission_rate": 0.001},
        3: {"initial_capital": 2000000.0, "commission_rate": 0.0008},
    }
    account_config = account_configs.get(account_id, account_configs[1])
    initial_capital = account_config["initial_capital"]
    commission_rate = account_config["commission_rate"]

    # 定义买卖信号触发点（模拟策略求值结果）
    # (bar索引, "buy"/"sell", 触发原因)
    # 这些信号放在价格走势的关键拐点上，模拟真实策略行为
    signal_points = {}

    # 找到关键的局部低点作为买入点，局部高点作为卖出点
    # 简化处理：手动指定bar索引，确保逻辑自洽
    total_days = len(trading_days)
    buy_points = [
        int(total_days * 0.05),   # 2月初，价格底部区域
        int(total_days * 0.32),   # 4月中，回调后
        int(total_days * 0.48),   # 6月初，回调底部
        int(total_days * 0.72),   # 9月初，回调底部
        int(total_days * 0.83),   # 10月中，反弹启动
    ]
    sell_points = [
        int(total_days * 0.18),   # 3月中，反弹高点
        int(total_days * 0.40),   # 5月底，上涨乏力
        int(total_days * 0.65),   # 8月中，高点
        int(total_days * 0.77),   # 9月底，继续下跌
        int(total_days * 0.92),   # 11月底，高点
    ]

    signal_reasons_buy = ["均线金叉", "RSI超卖反弹", "布林带下轨支撑", "MACD金叉", "KDJ金叉"]
    signal_reasons_sell = ["均线死叉", "RSI超买回落", "布林带上轨突破", "MACD死叉", "KDJ超买"]

    for i, idx in enumerate(buy_points):
        if idx < total_days:
            signal_points[idx] = ("buy", signal_reasons_buy[i])
    for i, idx in enumerate(sell_points):
        if idx < total_days:
            signal_points[idx] = ("sell", signal_reasons_sell[i])

    # ── 逐bar模拟回测 ──
    cash = initial_capital       # 当前现金余额
    position_qty = 0             # 当前持仓数量（股）
    position_cost = 0.0          # 持仓成本价（买入均价）
    bars_result = []             # 逐bar记录
    log_entries_global = []      # 全局日志收集

    # 配对追踪：记录最近一次买入的价格和手续费，用于计算卖出时的盈亏
    last_buy_price = 0.0
    last_buy_commission = 0.0
    last_buy_qty = 0

    for i, kbar in enumerate(kline_bars):
        day_orders = []          # 当日订单
        day_logs = []            # 当日日志
        signal = None
        signal_reason = None

        # 检查是否有信号
        if i in signal_points:
            signal, signal_reason = signal_points[i]
            current_price = kbar["close"]

            if signal == "buy" and position_qty == 0:
                # 买入：用可用资金的30%买入（简化仓位管理）
                # 真实场景中由策略模块内部的仓位管理决定
                buy_amount = cash * 0.3
                commission = round(buy_amount * commission_rate, 2)

                if buy_amount > commission:
                    # 扣除手续费后计算可买股数（100股整数倍）
                    available = buy_amount - commission
                    qty = int(available / current_price / 100) * 100

                    if qty > 0:
                        actual_amount = round(qty * current_price, 2)
                        actual_commission = round(actual_amount * commission_rate, 2)

                        # 执行买入
                        cash -= (actual_amount + actual_commission)
                        position_qty = qty
                        position_cost = current_price

                        # 记录买入成本，用于后续卖出计算盈亏
                        last_buy_price = current_price
                        last_buy_commission = actual_commission
                        last_buy_qty = qty

                        order = OrderRecord(
                            time=kbar["time"],
                            side="buy",
                            price=current_price,
                            quantity=qty,
                            amount=actual_amount,
                            commission=actual_commission,
                            pnl=0.0,  # 买入时无盈亏
                            signal=signal_reason,
                        )
                        day_orders.append(order)
                        day_logs.append(f"[TRADE] 买入 {stock_code} {qty}股 @{current_price}，手续费{actual_commission}")

            elif signal == "sell" and position_qty > 0:
                # 卖出：全部卖出
                sell_amount = round(position_qty * current_price, 2)
                commission = round(sell_amount * commission_rate, 2)

                # 计算盈亏 = (卖出价 - 买入价) × 数量 - 买入手续费 - 卖出手续费
                pnl = round(
                    (current_price - last_buy_price) * position_qty
                    - last_buy_commission
                    - commission,
                    2
                )

                # 执行卖出
                cash += (sell_amount - commission)

                order = OrderRecord(
                    time=kbar["time"],
                    side="sell",
                    price=current_price,
                    quantity=position_qty,
                    amount=sell_amount,
                    commission=commission,
                    pnl=pnl,
                    signal=signal_reason,
                )
                day_orders.append(order)

                pnl_sign = "+" if pnl >= 0 else ""
                day_logs.append(f"[TRADE] 卖出 {stock_code} {position_qty}股 @{current_price}，盈亏{pnl_sign}{pnl}，手续费{commission}")

                position_qty = 0
                position_cost = 0.0

            elif signal == "buy" and position_qty > 0:
                day_logs.append(f"[WARN] 已持有仓位，忽略买入信号：{signal_reason}")
            elif signal == "sell" and position_qty == 0:
                day_logs.append(f"[WARN] 无持仓，忽略卖出信号：{signal_reason}")

        # 计算当日账户状态
        if position_qty > 0:
            market_value = round(position_qty * kbar["close"], 2)
            floating_pnl = round(market_value - (position_cost * position_qty), 2)
            positions = [PositionRecord(
                stock_code=stock_code,
                quantity=position_qty,
                cost_price=position_cost,
                current_price=kbar["close"],
                market_value=market_value,
                floating_pnl=floating_pnl,
            )]
        else:
            market_value = 0.0
            positions = []

        total_assets = round(cash + market_value, 2)

        # 第一天加启动日志
        if i == 0:
            day_logs.insert(0, f"[INFO] 回测启动，策略：{strategy_names.get(strategy_id, '未知')}，股票：{stock_code}，初始资金：¥{initial_capital:,.0f}")

        # 最后一天加结束日志
        if i == len(kline_bars) - 1:
            day_logs.append(f"[INFO] 回测结束，共{len(kline_bars)}个交易日")

        bar = BarRecord(
            time=kbar["time"],
            open=kbar["open"],
            high=kbar["high"],
            low=kbar["low"],
            close=kbar["close"],
            volume=kbar["volume"],
            amount=kbar["amount"],
            benchmark_close=benchmark_prices.get(kbar["time"], 0.0),
            signal=signal,
            signal_reason=signal_reason,
            orders=day_orders,
            positions=positions,
            cash=round(cash, 2),
            total_assets=total_assets,
            log_entries=day_logs,
        )
        bars_result.append(bar)

    return BacktestResult(
        session_id=f"mock_{strategy_id}_{stock_code}",
        stock_code=stock_code,
        strategy_id=strategy_id,
        strategy_name=strategy_names.get(strategy_id, "模拟策略"),
        account_id=account_id,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        total_bars=len(bars_result),
        time_elapsed=round(len(bars_result) * 0.01, 2),  # 模拟耗时
        bars=bars_result,
    )
