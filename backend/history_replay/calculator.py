"""
回测指标计算器。

基于策略模块 run_backtest 返回的 BacktestResult 原始数据，
自行统计出报告页面需要的所有指标和图表数据。

设计原则：
- 输入：BacktestResult（策略模块返回的原始数据，不含任何统计指标）
- 输出：ReportMetrics（所有计算好的指标，可直接传给前端渲染）
- 所有指标在此计算，策略模块无需关心报告需求
"""

import math
from typing import Optional

# 类型契约统一到 strategy_engine.runtime.types（单源），避免两边重复定义
from strategy_engine.runtime.types import BacktestResult, BarRecord, OrderRecord, PositionRecord


# ============================================================
# 常量
# ============================================================

RISK_FREE_RATE_ANNUAL = 0.03    # 无风险年化收益率，取3年期国债收益率
                                 # 业务含义：资金不冒任何风险能获得的基准回报
                                 # 用途：夏普比率、索提诺比率、Alpha 的计算中扣除

TRADING_DAYS_PER_YEAR = 252     # 每年交易日数
                                 # 业务含义：A股市场一年大约252个交易日
                                 # 用途：日频指标年化时乘 √252，收益年化时乘 252/天数


# ============================================================
# 指标计算函数
# ============================================================

def calculate_daily_returns(bars: list[BarRecord]) -> list[float]:
    """
    计算策略每日收益率序列。

    业务含义：策略每天赚了或亏了多少百分比。
    这是计算夏普比率、索提诺比率、波动率、Alpha、Beta等几乎所有指标的基础输入。

    计算方式：
        当日收益率 = (当日总资产 / 前日总资产) - 1
        第一个交易日收益率记为0（因为没有"前日"做对比）

    用途：
    - 夏普比率：(平均日收益 - 无风险日收益) / 日收益标准差
    - 索提诺比率：(平均日收益 - 无风险日收益) / 下行标准差
    - 策略波动率：日收益标准差 × √252
    - Alpha/Beta：策略日收益与基准日收益的回归分析
    - 信息率：超额收益均值 / 跟踪误差
    """
    returns = []
    for i, bar in enumerate(bars):
        if i == 0:
            returns.append(0.0)
        else:
            prev_assets = bars[i - 1].total_assets
            if prev_assets > 0:
                daily_return = (bar.total_assets / prev_assets) - 1.0
            else:
                daily_return = 0.0
            returns.append(daily_return)
    return returns


def calculate_benchmark_daily_returns(bars: list[BarRecord]) -> list[float]:
    """
    计算基准每日收益率序列。

    业务含义：市场基准（如沪深300）每天涨了多少百分比。
    用于和策略收益对比，评估策略是否跑赢了市场。

    计算方式：
        基准日收益率 = (当日基准收盘价 / 前日基准收盘价) - 1

    用途：
    - 基准总收益率：(末基准价 / 首基准价) - 1
    - Alpha：策略超额收益中无法被市场解释的部分
    - Beta：策略收益对市场收益的敏感度
    - 信息率：超额收益的稳定性
    - 基准波动率：基准日收益标准差 × √252
    """
    returns = []
    for i, bar in enumerate(bars):
        if i == 0:
            returns.append(0.0)
        else:
            prev_close = bars[i - 1].benchmark_close
            if prev_close > 0:
                daily_return = (bar.benchmark_close / prev_close) - 1.0
            else:
                daily_return = 0.0
            returns.append(daily_return)
    return returns


def calculate_total_return(bars: list[BarRecord]) -> float:
    """
    计算策略总收益率（%）。

    业务含义：从回测开始到结束，策略总共赚了或亏了多少百分比。
    这是最直观的收益指标——"投了100万，最后变成多少"。

    计算方式：
        总收益率 = (期末总资产 / 期初总资产 - 1) × 100

    用途：报告核心指标栏"策略总收益"
    """
    if not bars or bars[0].total_assets == 0:
        return 0.0
    return round(
        (bars[-1].total_assets / bars[0].total_assets - 1.0) * 100,
        2
    )


def calculate_benchmark_return(bars: list[BarRecord]) -> float:
    """
    计算基准总收益率（%）。

    业务含义：同期市场基准（如沪深300）涨了多少百分比。
    用于判断策略是否跑赢了市场——策略收益 > 基准收益 = 跑赢。

    计算方式：
        基准收益率 = (期末基准价 / 期初基准价 - 1) × 100

    用途：报告核心指标栏"基准收益"、策略vs基准对比图
    """
    if not bars or bars[0].benchmark_close == 0:
        return 0.0
    return round(
        (bars[-1].benchmark_close / bars[0].benchmark_close - 1.0) * 100,
        2
    )


def calculate_annual_return(bars: list[BarRecord]) -> float:
    """
    计算年化收益率（%）。

    业务含义：把回测期间的总收益率折算成一年的收益率。
    便于不同时间长度的回测结果横向比较。
    比如3个月赚了8%，年化约34%（复利计算）。

    计算方式：
        年化收益率 = (1 + 总收益率)^(252/交易天数) - 1

    用途：报告核心指标栏"年化收益"
    """
    if len(bars) < 2:
        return 0.0
    total_return = bars[-1].total_assets / bars[0].total_assets - 1.0
    trading_days = len(bars)
    if total_return <= -1.0:
        return -100.0
    annual = (1.0 + total_return) ** (TRADING_DAYS_PER_YEAR / trading_days) - 1.0
    return round(annual * 100, 2)


def calculate_max_drawdown(bars: list[BarRecord]) -> float:
    """
    计算最大回撤（%）。

    业务含义：在回测期间，从任一历史最高点到后续最低点的最大跌幅。
    衡量策略可能面临的最坏情况——"最惨的时候亏了多少"。
    最大回撤越大，说明策略的极端风险越高。

    计算方式：
        1. 记录截止每个时点的历史最高总资产（峰值）
        2. 对每个时点，计算 (当前总资产 - 峰值) / 峰值
        3. 取所有回撤中最小（最负）的那个
        4. 结果取绝对值，以正数形式展示（如 -12.3% 展示为 12.3%）

    用途：报告核心指标栏"最大回撤"（蓝色高亮+tooltip）
    """
    if not bars:
        return 0.0

    peak = bars[0].total_assets    # 历史最高总资产
    max_dd = 0.0                    # 最大回撤（负数）

    for bar in bars:
        # 更新历史峰值
        if bar.total_assets > peak:
            peak = bar.total_assets

        # 计算当前回撤 = (当前资产 - 峰值) / 峰值
        if peak > 0:
            drawdown = (bar.total_assets - peak) / peak
            if drawdown < max_dd:
                max_dd = drawdown

    # 返回正数（回撤是亏损，展示为正数更直观）
    return round(abs(max_dd) * 100, 2)


def calculate_sharpe_ratio(daily_returns: list[float]) -> float:
    """
    计算夏普比率。

    业务含义：每承担一单位总风险，能获得多少超额收益。
    夏普比率越高，说明策略的风险调整后收益越好。
    一般认为：>1 好，>2 很好，>3 优秀。

    计算方式：
        夏普比率 = (平均日收益 - 无风险日收益) / 日收益标准差 × √252

        其中：
        - 无风险日收益 = (1 + 0.03)^(1/252) - 1 ≈ 0.000118
        - √252 用于将日频指标年化
        - 标准差衡量的是总风险（包括上行和下行波动）

    用途：报告核心指标栏"夏普比率"
    """
    if len(daily_returns) < 2:
        return 0.0

    # 无风险日收益率
    rf_daily = (1.0 + RISK_FREE_RATE_ANNUAL) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0

    # 平均日收益率
    mean_return = sum(daily_returns) / len(daily_returns)

    # 日收益率标准差（总风险）
    variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_dev = math.sqrt(variance) if variance > 0 else 0.0

    if std_dev == 0:
        return 0.0

    sharpe = (mean_return - rf_daily) / std_dev * math.sqrt(TRADING_DAYS_PER_YEAR)
    return round(sharpe, 3)


def calculate_sortino_ratio(daily_returns: list[float]) -> float:
    """
    计算索提诺比率。

    业务含义：与夏普比率类似，但只考虑下行风险（亏损的波动）。
    夏普比率对上行和下行波动一视同仁，而索提诺比率认为"涨得快"不是风险，
    只有"跌得深"才是风险。对于收益分布不对称的策略，索提诺更合理。

    计算方式：
        索提诺比率 = (平均日收益 - 无风险日收益) / 下行标准差 × √252

        其中：
        - 下行标准差 = sqrt(sum(min(r - rf, 0)^2) / N)
        - 只计算低于无风险收益率的部分，高于的不算风险

    用途：报告核心指标栏"索提诺比率"
    """
    if len(daily_returns) < 2:
        return 0.0

    rf_daily = (1.0 + RISK_FREE_RATE_ANNUAL) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0
    mean_return = sum(daily_returns) / len(daily_returns)

    # 只计算低于无风险收益率的偏离（下行风险）
    downside_diffs = [min(r - rf_daily, 0.0) for r in daily_returns]
    downside_var = sum(d ** 2 for d in downside_diffs) / len(downside_diffs)
    downside_std = math.sqrt(downside_var) if downside_var > 0 else 0.0

    if downside_std == 0:
        return 0.0

    sortino = (mean_return - rf_daily) / downside_std * math.sqrt(TRADING_DAYS_PER_YEAR)
    return round(sortino, 3)


def calculate_alpha_beta(
    strategy_returns: list[float],
    benchmark_returns: list[float],
) -> tuple[float, float]:
    """
    计算 Alpha 和 Beta。

    Beta 业务含义：策略收益对市场收益的敏感度。
        - Beta = 1：策略与市场同涨同跌
        - Beta > 1：策略比市场波动更大（放大器）
        - Beta < 1：策略比市场波动更小（缓冲器）
        - Beta < 0：策略与市场反向

    Alpha 业务含义：策略超额收益中无法被市场波动解释的部分。
        - Alpha > 0：策略跑赢了市场（经风险调整后仍有正收益）
        - Alpha < 0：策略跑输了市场
        这是衡量基金经理/策略"真本事"的核心指标

    计算方式（基于CAPM模型的线性回归）：
        Beta = Cov(策略日收益, 基准日收益) / Var(基准日收益)
        Alpha = 策略年化超额收益 - Beta × 基准年化超额收益

    用途：
    - Alpha：报告核心指标栏"阿尔法"
    - Beta：报告核心指标栏"贝塔"
    """
    n = min(len(strategy_returns), len(benchmark_returns))
    if n < 2:
        return 0.0, 0.0

    strat = strategy_returns[:n]
    bench = benchmark_returns[:n]

    # 计算均值
    mean_strat = sum(strat) / n
    mean_bench = sum(bench) / n

    # 计算协方差和方差
    cov = sum((s - mean_strat) * (b - mean_bench) for s, b in zip(strat, bench)) / (n - 1)
    var_bench = sum((b - mean_bench) ** 2 for b in bench) / (n - 1)

    # Beta = 协方差 / 方差
    if var_bench == 0:
        beta = 0.0
    else:
        beta = cov / var_bench

    # Alpha = 策略年化超额收益 - Beta × 基准年化超额收益
    rf_daily = (1.0 + RISK_FREE_RATE_ANNUAL) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0
    strat_annual_excess = (mean_strat - rf_daily) * TRADING_DAYS_PER_YEAR
    bench_annual_excess = (mean_bench - rf_daily) * TRADING_DAYS_PER_YEAR
    alpha = strat_annual_excess - beta * bench_annual_excess

    return round(alpha, 3), round(beta, 3)


def calculate_information_ratio(
    strategy_returns: list[float],
    benchmark_returns: list[float],
) -> float:
    """
    计算信息率（Information Ratio）。

    业务含义：策略超额收益的稳定性。
    衡量策略"持续跑赢基准"的能力——不仅跑赢了，而且是稳定地跑赢，
    不是靠某几次大赢弥补多次小输。

    计算方式：
        超额收益 = 策略日收益 - 基准日收益
        信息率 = 超额收益均值 / 超额收益标准差（跟踪误差）

        跟踪误差 = 超额收益的标准差，衡量策略偏离基准的程度

    用途：报告核心指标栏"信息率"
    """
    n = min(len(strategy_returns), len(benchmark_returns))
    if n < 2:
        return 0.0

    # 每日超额收益
    excess_returns = [strategy_returns[i] - benchmark_returns[i] for i in range(n)]

    # 超额收益均值
    mean_excess = sum(excess_returns) / n

    # 跟踪误差（超额收益标准差）
    var_excess = sum((r - mean_excess) ** 2 for r in excess_returns) / (n - 1)
    tracking_error = math.sqrt(var_excess) if var_excess > 0 else 0.0

    if tracking_error == 0:
        return 0.0

    # 年化信息率
    ir = (mean_excess / tracking_error) * math.sqrt(TRADING_DAYS_PER_YEAR)
    return round(ir, 3)


def calculate_volatility(daily_returns: list[float]) -> float:
    """
    计算年化波动率。

    业务含义：策略收益率的波动程度。波动率越高，收益越不稳定，
    投资者需要承受更大的净值起伏。波动率本身不区分上行和下行。

    计算方式：
        日收益率标准差 × √252
        乘 √252 是为了将日频波动率年化

    用途：报告核心指标栏"策略波动率"
    """
    if len(daily_returns) < 2:
        return 0.0

    mean_return = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_dev = math.sqrt(variance) if variance > 0 else 0.0

    return round(std_dev * math.sqrt(TRADING_DAYS_PER_YEAR), 3)


def calculate_win_rate(bars: list[BarRecord]) -> float:
    """
    计算胜率（%）。

    业务含义：盈利交易占总交易的比例。
    胜率60%意味着10笔交易中有6笔是赚钱的。
    胜率高不一定赚钱（可能赢小亏大），胜率低不一定亏钱（可能赢大亏小）。

    计算方式：
        胜率 = 盈利卖出次数 / 总卖出次数 × 100
        只统计卖出订单，因为只有卖出才能确定一笔交易是盈是亏

    用途：报告指标"胜率"
    """
    sell_orders = []
    for bar in bars:
        for order in bar.orders:
            if order.side == "sell":
                sell_orders.append(order)

    if not sell_orders:
        return 0.0

    win_count = sum(1 for o in sell_orders if o.pnl > 0)
    return round(win_count / len(sell_orders) * 100, 1)


def calculate_profit_loss_ratio(bars: list[BarRecord]) -> float:
    """
    计算盈亏比。

    业务含义：平均每笔盈利交易的盈利金额 / 平均每笔亏损交易的亏损金额。
    盈亏比2.0意味着"赢的时候赚的是亏的时候亏的2倍"。
    配合胜率可以判断策略是否可持续：胜率50%+盈亏比2.0就是好策略。

    计算方式：
        盈亏比 = avg(盈利pnl) / |avg(亏损pnl)|
        只统计卖出订单的pnl

    用途：报告指标"盈亏比"
    """
    sell_orders = []
    for bar in bars:
        for order in bar.orders:
            if order.side == "sell":
                sell_orders.append(order)

    if not sell_orders:
        return 0.0

    profits = [o.pnl for o in sell_orders if o.pnl > 0]
    losses = [o.pnl for o in sell_orders if o.pnl < 0]

    if not profits or not losses:
        # 全赢或全输，盈亏比无意义
        return 0.0

    avg_profit = sum(profits) / len(profits)
    avg_loss = abs(sum(losses) / len(losses))

    if avg_loss == 0:
        return 0.0

    return round(avg_profit / avg_loss, 2)


def calculate_trade_count(bars: list[BarRecord]) -> int:
    """
    计算完整交易次数。

    业务含义：一买一卖算一笔完整交易。
    只统计卖出次数，因为每次卖出意味着一笔交易完成。

    计算方式：count(side == "sell")

    用途：报告指标"交易次数"
    """
    count = 0
    for bar in bars:
        for order in bar.orders:
            if order.side == "sell":
                count += 1
    return count


def calculate_total_pnl(bars: list[BarRecord]) -> float:
    """
    计算总盈亏金额。

    业务含义：所有交易赚了或亏了多少钱（已实现盈亏）。
    注意：这是卖出时的已实现盈亏之和，不包括持仓浮盈。

    计算方式：sum(所有卖出订单的pnl)

    用途：报告指标"总盈亏"
    """
    total = 0.0
    for bar in bars:
        for order in bar.orders:
            if order.side == "sell":
                total += order.pnl
    return round(total, 2)


def calculate_daily_pnl_series(bars: list[BarRecord]) -> list[dict]:
    """
    计算每日盈亏金额序列。

    业务含义：每个交易日赚了或亏了多少钱。
    正数为盈利，负数为亏损。

    计算方式：
        当日盈亏 = 当日总资产 - 前日总资产
        第一天盈亏记为0

    用途：每日盈亏柱状图（绿色柱=盈利，红色柱=亏损）
    """
    result = []
    for i, bar in enumerate(bars):
        if i == 0:
            pnl = 0.0
        else:
            pnl = bar.total_assets - bars[i - 1].total_assets

        # 当日买入/卖出金额（用于图表展示）
        buy_amount = sum(o.amount for o in bar.orders if o.side == "buy")
        sell_amount = sum(o.amount for o in bar.orders if o.side == "sell")

        result.append({
            "time": bar.time,
            "pnl": round(pnl, 2),
            "buy_amount": round(buy_amount, 2),
            "sell_amount": round(sell_amount, 2),
        })
    return result


def calculate_equity_curve(bars: list[BarRecord]) -> list[dict]:
    """
    计算资金曲线。

    业务含义：账户总资产随时间的变化轨迹。
    从中可以看到资产的增长/回撤过程。

    计算方式：
        净值 = 当日总资产 / 期初总资产 × 1000000（以初始资金为基准）
        回撤 = (当前净值 - 历史最高净值) / 历史最高净值 × 100

    用途：回放视图的资金曲线图（净值+回撤双轴）
    """
    if not bars or bars[0].total_assets == 0:
        return []

    initial = bars[0].total_assets
    peak = initial
    result = []

    for bar in bars:
        equity = bar.total_assets
        if equity > peak:
            peak = equity
        drawdown = (equity - peak) / peak * 100 if peak > 0 else 0.0

        result.append({
            "time": bar.time,
            "equity": round(equity, 2),
            "drawdown": round(drawdown, 2),
        })
    return result


def calculate_strategy_return_curve(bars: list[BarRecord]) -> list[dict]:
    """
    计算策略累计收益率曲线。

    业务含义：策略从回测开始到每个时间点的累计收益百分比。
    如第50天累计收益5%，意味着到第50天策略总共赚了5%。

    计算方式：
        累计收益率 = (当日总资产 / 期初总资产 - 1) × 100

    用途：报告视图的策略vs基准收益对比图（蓝色曲线）
    """
    if not bars or bars[0].total_assets == 0:
        return []

    initial = bars[0].total_assets
    return [
        {
            "time": bar.time,
            "return_pct": round((bar.total_assets / initial - 1.0) * 100, 2),
        }
        for bar in bars
    ]


def calculate_benchmark_return_curve(bars: list[BarRecord]) -> list[dict]:
    """
    计算基准累计收益率曲线。

    业务含义：市场基准从回测开始到每个时间点的累计收益百分比。

    计算方式：
        基准累计收益率 = (当日基准价 / 期初基准价 - 1) × 100

    用途：报告视图的策略vs基准收益对比图（红色曲线）
    """
    if not bars or bars[0].benchmark_close == 0:
        return []

    initial = bars[0].benchmark_close
    return [
        {
            "time": bar.time,
            "return_pct": round((bar.benchmark_close / initial - 1.0) * 100, 2),
        }
        for bar in bars
    ]


def calculate_daily_position_series(bars: list[BarRecord]) -> list[dict]:
    """
    计算每日持仓&收益数据序列。

    业务含义：每个交易日收盘时的持仓状态和当日收益。

    用途：报告视图的"每日持仓&收益"表
    """
    result = []
    for i, bar in enumerate(bars):
        # 当日收益率
        if i == 0 or bars[i - 1].total_assets == 0:
            daily_return_pct = 0.0
        else:
            daily_return_pct = (bar.total_assets - bars[i - 1].total_assets) / bars[i - 1].total_assets * 100

        # 当日盈亏
        if i == 0:
            daily_pnl = 0.0
        else:
            daily_pnl = bar.total_assets - bars[i - 1].total_assets

        # 持仓信息（取第一个持仓，当前只支持单股票）
        position = bar.positions[0] if bar.positions else None

        result.append({
            "time": bar.time,
            "quantity": position.quantity if position else 0,
            "market_value": position.market_value if position else 0.0,
            "daily_pnl": round(daily_pnl, 2),
            "daily_return_pct": round(daily_return_pct, 2),
            "total_equity": bar.total_assets,
        })
    return result


def collect_log_entries(bars: list[BarRecord]) -> list[dict]:
    """
    从所有bar中收集日志条目。

    业务含义：回测过程中产生的运行日志，包括信息、警告、错误。
    帮助用户理解回测过程中发生了什么。

    用途：报告视图的"日志输出"面板
    """
    logs = []
    for bar in bars:
        for entry in bar.log_entries:
            # 解析日志级别
            if "[ERROR]" in entry:
                level = "error"
            elif "[WARN]" in entry:
                level = "warn"
            else:
                level = "info"
            logs.append({
                "time": bar.time,
                "level": level,
                "message": entry,
            })
    return logs


def collect_trade_records(bars: list[BarRecord], stock_code: str) -> list[dict]:
    """
    从所有bar中收集交易记录。

    业务含义：回测过程中发生的所有买卖交易的完整列表。

    用途：报告视图的"交易详情"表
    """
    records = []
    order_id = 1
    for bar in bars:
        for order in bar.orders:
            records.append({
                "id": order_id,
                "time": order.time,
                "side": order.side,
                "stock_code": stock_code,
                "price": order.price,
                "quantity": order.quantity,
                "amount": order.amount,
                "commission": order.commission,
                "pnl": order.pnl,
                "signal": order.signal,
            })
            order_id += 1
    return records


def collect_kline_data(bars: list[BarRecord]) -> list[dict]:
    """
    从所有bar中提取K线数据。

    业务含义：用于回放视图的K线图渲染。

    用途：ReplayChart 组件
    """
    return [
        {
            "time": bar.time,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in bars
    ]


def collect_trade_signals(bars: list[BarRecord]) -> list[dict]:
    """
    从所有bar中提取买卖信号。

    业务含义：K线图上标记的买卖点。

    用途：ReplayChart 买卖标记
    """
    signals = []
    for bar in bars:
        if bar.signal in ("buy", "sell") and bar.orders:
            order = bar.orders[0]  # 取第一笔订单
            signals.append({
                "time": bar.time,
                "side": bar.signal,
                "price": order.price,
                "quantity": order.quantity,
                "signal": bar.signal_reason or "",
            })
    return signals


# ============================================================
# 统一入口：一次调用计算所有指标和图表数据
# ============================================================

class ReportMetrics:
    """
    回测报告的完整指标和图表数据。

    所有字段由 calculate_report_metrics 根据策略模块返回的
    BacktestResult 原始数据计算得出，可直接传给前端渲染。
    """

    # 基础信息
    initial_capital: float        # 期初总资产
    final_capital: float          # 期末总资产
    time_elapsed: float           # 回测耗时（秒）

    # 核心指标
    total_return: float           # 策略总收益率 %
    benchmark_return: float       # 基准总收益率 %
    annual_return: float          # 年化收益率 %
    max_drawdown: float           # 最大回撤 %
    sharpe_ratio: float           # 夏普比率
    sortino_ratio: float          # 索提诺比率
    alpha: float                  # Alpha
    beta: float                   # Beta
    information_ratio: float      # 信息率
    strategy_volatility: float    # 策略波动率
    benchmark_volatility: float   # 基准波动率
    win_rate: float               # 胜率 %
    profit_loss_ratio: float      # 盈亏比
    trade_count: int              # 交易次数
    total_pnl: float              # 总盈亏金额

    # 图表数据
    kline_data: list              # K线数据
    trade_signals: list           # 买卖信号
    equity_curve: list            # 资金曲线
    strategy_return_curve: list   # 策略累计收益曲线
    benchmark_return_curve: list  # 基准累计收益曲线
    daily_pnl_series: list        # 每日盈亏
    daily_position_series: list   # 每日持仓
    trade_records: list           # 交易详情
    log_entries: list             # 日志

    def to_dict(self) -> dict:
        """转换为dict，方便序列化返回前端"""
        return {
            "initial_capital": self.initial_capital,
            "final_capital": self.final_capital,
            "time_elapsed": self.time_elapsed,
            "total_return": self.total_return,
            "benchmark_return": self.benchmark_return,
            "annual_return": self.annual_return,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "alpha": self.alpha,
            "beta": self.beta,
            "information_ratio": self.information_ratio,
            "strategy_volatility": self.strategy_volatility,
            "benchmark_volatility": self.benchmark_volatility,
            "win_rate": self.win_rate,
            "profit_loss_ratio": self.profit_loss_ratio,
            "trade_count": self.trade_count,
            "total_pnl": self.total_pnl,
            "kline_data": self.kline_data,
            "trade_signals": self.trade_signals,
            "equity_curve": self.equity_curve,
            "strategy_return_curve": self.strategy_return_curve,
            "benchmark_return_curve": self.benchmark_return_curve,
            "daily_pnl_series": self.daily_pnl_series,
            "daily_position_series": self.daily_position_series,
            "trade_records": self.trade_records,
            "log_entries": self.log_entries,
        }


def calculate_report_metrics(result: BacktestResult) -> ReportMetrics:
    """
    根据策略模块返回的 BacktestResult，计算报告需要的所有指标和图表数据。

    这是指标计算的统一入口。调用此函数一次，即可获得完整的报告数据。

    参数：
        result: 策略模块 run_backtest 的返回值，包含逐bar原始明细

    返回：
        ReportMetrics: 所有计算好的指标和图表数据，可直接传给前端
    """
    bars = result.bars

    if not bars:
        # 空回测结果，返回零值
        metrics = ReportMetrics()
        for attr in [
            "initial_capital", "final_capital", "time_elapsed",
            "total_return", "benchmark_return", "annual_return", "max_drawdown",
            "sharpe_ratio", "sortino_ratio", "alpha", "beta",
            "information_ratio", "strategy_volatility", "benchmark_volatility",
            "win_rate", "profit_loss_ratio", "total_pnl",
        ]:
            setattr(metrics, attr, 0.0)
        metrics.trade_count = 0
        for attr in [
            "kline_data", "trade_signals", "equity_curve",
            "strategy_return_curve", "benchmark_return_curve",
            "daily_pnl_series", "daily_position_series",
            "trade_records", "log_entries",
        ]:
            setattr(metrics, attr, [])
        return metrics

    # ── 计算每日收益率序列（多个指标的基础输入） ──
    strategy_daily_returns = calculate_daily_returns(bars)
    benchmark_daily_returns = calculate_benchmark_daily_returns(bars)

    # ── 核心指标 ──
    alpha, beta = calculate_alpha_beta(strategy_daily_returns, benchmark_daily_returns)

    metrics = ReportMetrics()
    metrics.initial_capital = bars[0].total_assets
    metrics.final_capital = bars[-1].total_assets
    metrics.time_elapsed = result.time_elapsed

    metrics.total_return = calculate_total_return(bars)
    metrics.benchmark_return = calculate_benchmark_return(bars)
    metrics.annual_return = calculate_annual_return(bars)
    metrics.max_drawdown = calculate_max_drawdown(bars)
    metrics.sharpe_ratio = calculate_sharpe_ratio(strategy_daily_returns)
    metrics.sortino_ratio = calculate_sortino_ratio(strategy_daily_returns)
    metrics.alpha = alpha
    metrics.beta = beta
    metrics.information_ratio = calculate_information_ratio(strategy_daily_returns, benchmark_daily_returns)
    metrics.strategy_volatility = calculate_volatility(strategy_daily_returns)
    metrics.benchmark_volatility = calculate_volatility(benchmark_daily_returns)
    metrics.win_rate = calculate_win_rate(bars)
    metrics.profit_loss_ratio = calculate_profit_loss_ratio(bars)
    metrics.trade_count = calculate_trade_count(bars)
    metrics.total_pnl = calculate_total_pnl(bars)

    # ── 图表数据 ──
    metrics.kline_data = collect_kline_data(bars)
    metrics.trade_signals = collect_trade_signals(bars)
    metrics.equity_curve = calculate_equity_curve(bars)
    metrics.strategy_return_curve = calculate_strategy_return_curve(bars)
    metrics.benchmark_return_curve = calculate_benchmark_return_curve(bars)
    metrics.daily_pnl_series = calculate_daily_pnl_series(bars)
    metrics.daily_position_series = calculate_daily_position_series(bars)
    metrics.trade_records = collect_trade_records(bars, result.stock_code)
    metrics.log_entries = collect_log_entries(bars)

    return metrics
