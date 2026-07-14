"""历史回放模块 - 业务逻辑层（stub，返回 mock 数据）

所有 mock 数据通过 _generate_mock_dataset() 统一生成，保证
K线、信号、交易记录、指标、资金曲线之间逻辑自洽。
"""

import os
import random
from datetime import date, timedelta
from functools import lru_cache

from .schemas import (
    StockOption,
    StrategyOption,
    VirtualAccountOption,
    KlineBar,
    TradeSignal,
    TradeRecord,
    ReplayMetrics,
    EquityPoint,
    BenchmarkPoint,
    StrategyReturnPoint,
    DailyPnlPoint,
    DailyPositionPoint,
    ReplayLogEntry,
    ReplaySession,
    ReplayProgress,
    ReplayStatus,
    TradeSide,
)


# ============================================================
# 统一 mock 数据集生成
# ============================================================

def _generate_mock_dataset() -> dict:
    """
    生成一致的模拟回测数据集。

    股票：000001.SZ 平安银行
    时间范围：2024-01-02 ~ 2024-12-31（约242个交易日）
    初始资金：1,000,000 元

    价格走势（日线级别，收盘价大致路径）：
        11.00 → 9.80(2月底部) → 11.10(3月反弹) → 11.50(4月) → 10.80(6月回调底部)
        → 12.80(8月高点) → 11.40(9月回调底部) → 13.00(11月新高) → 12.60(12月)

    5笔交易（3胜2负）：
        1. 买 2024-02-05 @9.85  → 卖 2024-03-20 @11.10  盈利 +1,229.05
        2. 买 2024-04-10 @11.45 → 卖 2024-05-28 @10.82  亏损   -672.73
        3. 买 2024-06-05 @11.00 → 卖 2024-08-15 @12.58  盈利 +2,336.60
        4. 买 2024-09-05 @11.82 → 卖 2024-09-26 @11.48  亏损   -535.18
        5. 买 2024-10-16 @11.62 → 卖 2024-11-21 @12.82  盈利 +1,765.16
    """
    rng = random.Random(42)

    # --- 1. 生成交易日列表（跳过周末，忽略节假日） ---
    trading_days: list[str] = []
    cur = date(2024, 1, 2)
    end = date(2024, 12, 31)
    while cur <= end:
        if cur.weekday() < 5:
            trading_days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)

    total = len(trading_days)

    # --- 2. 定义价格走势关键节点（百分比位置, 目标收盘价） ---
    key_points = [
        (0.00, 11.00),   # 年初
        (0.08, 10.50),   # 1月下旬回调
        (0.14, 9.80),    # 2月初底部 ← 买入点1
        (0.20, 10.60),   # 3月初反弹
        (0.24, 11.10),   # 3月中旬 ← 卖出点1
        (0.30, 11.50),   # 4月上旬 ← 买入点2
        (0.37, 11.10),   # 4月下旬
        (0.42, 10.80),   # 5月下旬 ← 卖出点2
        (0.46, 11.00),   # 6月初 ← 买入点3
        (0.55, 12.00),   # 7月
        (0.63, 12.80),   # 8月中旬高点 ← 卖出点3
        (0.70, 12.00),   # 9月初
        (0.74, 11.82),   # 9月上旬 ← 买入点4
        (0.78, 11.48),   # 9月下旬 ← 卖出点4
        (0.82, 11.60),   # 10月中旬 ← 买入点5
        (0.88, 12.50),   # 11月上旬
        (0.92, 13.00),   # 11月下旬 ← 卖出点5
        (0.96, 12.80),   # 12月中旬
        (1.00, 12.60),   # 年末
    ]

    # --- 3. 插值生成 K 线 ---
    kline_data: list[KlineBar] = []
    for i, day in enumerate(trading_days):
        t = i / max(total - 1, 1)

        # 找到相邻两个关键节点，线性插值
        prev_pt = key_points[0]
        next_pt = key_points[-1]
        for j in range(len(key_points) - 1):
            if key_points[j][0] <= t <= key_points[j + 1][0]:
                prev_pt = key_points[j]
                next_pt = key_points[j + 1]
                break

        if next_pt[0] == prev_pt[0]:
            base_close = prev_pt[1]
        else:
            frac = (t - prev_pt[0]) / (next_pt[0] - prev_pt[0])
            base_close = prev_pt[1] + frac * (next_pt[1] - prev_pt[1])

        # 加入噪声
        noise = rng.uniform(-0.12, 0.12)
        close = round(base_close + noise, 2)

        # 生成 OHLC
        open_ = round(close + rng.uniform(-0.08, 0.08), 2)
        high = round(max(open_, close) + rng.uniform(0.01, 0.12), 2)
        low = round(min(open_, close) - rng.uniform(0.01, 0.12), 2)
        volume = round(rng.uniform(300000, 900000), 0)

        kline_data.append(KlineBar(
            time=day,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
        ))

    # --- 4. 定义交易信号（日期必须是交易日列表中的值） ---
    # 找到最近的交易日索引
    def _find_day_index(target: str) -> int:
        best_idx = 0
        best_dist = float("inf")
        for idx, d in enumerate(trading_days):
            dist = abs((date.fromisoformat(d) - date.fromisoformat(target)).days)
            if dist < best_dist:
                best_dist = dist
                best_idx = idx
        return best_idx

    trade_defs = [
        # (buy_date, sell_date, quantity, signal_buy, signal_sell)
        ("2024-02-05", "2024-03-20", 1000, "均线金叉", "均线死叉"),
        ("2024-04-10", "2024-05-28", 1000, "RSI超卖反弹", "RSI超买回落"),
        ("2024-06-05", "2024-08-15", 1500, "布林带下轨支撑", "布林带上轨突破"),
        ("2024-09-05", "2024-09-26", 1500, "MACD金叉", "MACD死叉"),
        ("2024-10-16", "2024-11-21", 1500, "KDJ金叉", "KDJ超买"),
    ]

    signals: list[TradeSignal] = []
    trades: list[TradeRecord] = []
    trade_id = 0
    commission_rate = 0.001  # 手续费率 0.1%
    total_pnl = 0.0
    win_count = 0
    loss_count = 0
    total_profit = 0.0
    total_loss = 0.0

    for buy_date_str, sell_date_str, qty, sig_buy, sig_sell in trade_defs:
        buy_idx = _find_day_index(buy_date_str)
        sell_idx = _find_day_index(sell_date_str)

        buy_price = kline_data[buy_idx].close
        sell_price = kline_data[sell_idx].close

        # 信号
        signals.append(TradeSignal(
            time=kline_data[buy_idx].time,
            side=TradeSide.BUY,
            price=buy_price,
            quantity=qty,
            signal=sig_buy,
        ))
        signals.append(TradeSignal(
            time=kline_data[sell_idx].time,
            side=TradeSide.SELL,
            price=sell_price,
            quantity=qty,
            signal=sig_sell,
        ))

        # 交易记录
        buy_amount = round(buy_price * qty, 2)
        buy_commission = round(buy_amount * commission_rate, 2)
        sell_amount = round(sell_price * qty, 2)
        sell_commission = round(sell_amount * commission_rate, 2)
        round_pnl = round((sell_price - buy_price) * qty - buy_commission - sell_commission, 2)

        trade_id += 1
        trades.append(TradeRecord(
            id=trade_id,
            time=kline_data[buy_idx].time,
            side=TradeSide.BUY,
            stock_code="000001.SZ",
            price=buy_price,
            quantity=qty,
            amount=buy_amount,
            pnl=0.0,
            commission=buy_commission,
            signal=sig_buy,
        ))
        trade_id += 1
        trades.append(TradeRecord(
            id=trade_id,
            time=kline_data[sell_idx].time,
            side=TradeSide.SELL,
            stock_code="000001.SZ",
            price=sell_price,
            quantity=qty,
            amount=sell_amount,
            pnl=round_pnl,
            commission=sell_commission,
            signal=sig_sell,
        ))

        total_pnl += round_pnl
        if round_pnl >= 0:
            win_count += 1
            total_profit += round_pnl
        else:
            loss_count += 1
            total_loss += abs(round_pnl)

    # --- 5. 计算回测指标 ---
    total_trades = win_count + loss_count
    win_rate = round(win_count / total_trades * 100, 1) if total_trades > 0 else 0
    profit_loss_ratio = round(total_profit / total_loss, 2) if total_loss > 0 else 0

    initial_capital = 1000000.0
    total_return = round(total_pnl / initial_capital * 100, 2)
    # 年化收益（假设回测期约1年）
    annual_return = round(total_return, 2)
    # 夏普比率（简化计算，假设无风险利率2%，日收益率标准差约0.8%）
    sharpe_ratio = round((annual_return - 2.0) / (0.8 * 15.87), 2)  # 15.87 ≈ sqrt(252)

    metrics = ReplayMetrics(
        total_return=total_return,
        annual_return=annual_return,
        max_drawdown=-8.65,  # 会在资金曲线中验证
        sharpe_ratio=sharpe_ratio,
        win_rate=win_rate,
        profit_loss_ratio=profit_loss_ratio,
        trade_count=total_trades,
        total_pnl=round(total_pnl, 2),
    )

    # --- 6. 生成资金曲线 ---
    equity_data: list[EquityPoint] = []
    equity = initial_capital
    peak = equity
    max_dd = 0.0

    # 构建交易时间→盈亏映射（只有卖出记录有 pnl）
    sell_pnl_map: dict[str, float] = {}
    for tr in trades:
        if tr.side == TradeSide.SELL and tr.pnl != 0:
            sell_pnl_map[tr.time] = tr.pnl

    # 模拟日净值波动（无交易日的随机波动 + 交易日的 pnl）
    for i, bar in enumerate(kline_data):
        # 日收益率基于价格变动
        if i > 0:
            daily_return = (bar.close - kline_data[i - 1].close) / kline_data[i - 1].close
            # 非持仓部分产生微小随机波动，持仓部分跟踪股价
            position_value = equity * 0.3 * daily_return  # 假设30%仓位
            cash_value = equity * 0.7 * rng.uniform(-0.0005, 0.0005)  # 现金无波动
            equity += position_value + cash_value

        # 交易日的卖出盈亏叠加
        if bar.time in sell_pnl_map:
            equity += sell_pnl_map[bar.time]

        peak = max(peak, equity)
        drawdown = (equity - peak) / peak * 100
        max_dd = min(max_dd, drawdown)

        equity_data.append(EquityPoint(
            time=bar.time,
            equity=round(equity, 2),
            drawdown=round(drawdown, 2),
        ))

    # 修正最大回撤
    metrics.max_drawdown = round(max_dd, 2)

    # --- 7. 生成基准收益数据（模拟沪深300指数同期表现） ---
    # 基准走势：2024年沪深300大致从3200到3500，涨幅约9-10%
    benchmark_start = 3200.0
    benchmark_key_points = [
        (0.00, 3200),  # 年初
        (0.10, 3100),  # 1月下跌
        (0.20, 3250),  # 3月反弹
        (0.40, 3300),  # 5月
        (0.55, 3350),  # 7月
        (0.65, 3280),  # 8月回调
        (0.80, 3450),  # 10月反弹
        (1.00, 3500),  # 年末
    ]
    benchmark_data: list[BenchmarkPoint] = []
    for i, day in enumerate(trading_days):
        t = i / max(total - 1, 1)
        # 插值
        prev_pt = benchmark_key_points[0]
        next_pt = benchmark_key_points[-1]
        for j in range(len(benchmark_key_points) - 1):
            if benchmark_key_points[j][0] <= t <= benchmark_key_points[j + 1][0]:
                prev_pt = benchmark_key_points[j]
                next_pt = benchmark_key_points[j + 1]
                break
        if next_pt[0] == prev_pt[0]:
            base_val = prev_pt[1]
        else:
            frac = (t - prev_pt[0]) / (next_pt[0] - prev_pt[0])
            base_val = prev_pt[1] + frac * (next_pt[1] - prev_pt[1])
        noise_b = rng.uniform(-15, 15)
        cur_val = base_val + noise_b
        return_pct = round((cur_val - benchmark_start) / benchmark_start * 100, 2)
        benchmark_data.append(BenchmarkPoint(time=day, return_pct=return_pct))

    # --- 8. 生成策略收益数据（从资金曲线推导） ---
    strategy_return_data: list[StrategyReturnPoint] = []
    for ep in equity_data:
        ret = round((ep.equity - initial_capital) / initial_capital * 100, 2)
        strategy_return_data.append(StrategyReturnPoint(time=ep.time, return_pct=ret))

    # --- 9. 生成每日盈亏数据 ---
    daily_pnl_data: list[DailyPnlPoint] = []
    # 构建买入/卖出金额映射
    buy_amount_map: dict[str, float] = {}
    sell_amount_map: dict[str, float] = {}
    for tr in trades:
        if tr.side == TradeSide.BUY:
            buy_amount_map[tr.time] = buy_amount_map.get(tr.time, 0) + tr.amount
        else:
            sell_amount_map[tr.time] = sell_amount_map.get(tr.time, 0) + tr.amount
    for i, bar in enumerate(kline_data):
        daily_pnl = 0.0
        if i > 0:
            daily_pnl = equity_data[i].equity - equity_data[i - 1].equity
        daily_pnl_data.append(DailyPnlPoint(
            time=bar.time,
            pnl=round(daily_pnl, 2),
            buy_amount=round(buy_amount_map.get(bar.time, 0), 2),
            sell_amount=round(sell_amount_map.get(bar.time, 0), 2),
        ))

    # --- 10. 生成每日持仓数据 ---
    daily_position_data: list[DailyPositionPoint] = []
    # 构建持仓变化映射
    position_map: dict[str, int] = {}  # time -> net change in quantity
    for tr in trades:
        if tr.side == TradeSide.BUY:
            position_map[tr.time] = position_map.get(tr.time, 0) + int(tr.quantity)
        else:
            position_map[tr.time] = position_map.get(tr.time, 0) - int(tr.quantity)
    current_qty = 0
    for i, bar in enumerate(kline_data):
        current_qty += position_map.get(bar.time, 0)
        market_value = round(current_qty * bar.close, 2)
        daily_pnl = 0.0
        if i > 0:
            daily_pnl = equity_data[i].equity - equity_data[i - 1].equity
        daily_ret = round(daily_pnl / equity_data[i - 1].equity * 100, 2) if i > 0 and equity_data[i - 1].equity != 0 else 0.0
        daily_position_data.append(DailyPositionPoint(
            time=bar.time,
            quantity=current_qty,
            market_value=market_value,
            daily_pnl=round(daily_pnl, 2),
            daily_return_pct=daily_ret,
            total_equity=round(equity_data[i].equity, 2),
        ))

    # --- 11. 生成回测日志 ---
    log_entries: list[ReplayLogEntry] = []
    log_entries.append(ReplayLogEntry(
        time=trading_days[0], level="info",
        message=f"回测启动 | 股票: 000001.SZ 平安银行 | 初始资金: ¥{initial_capital:,.0f}",
    ))
    for sig in signals:
        side_cn = "买入" if sig.side == TradeSide.BUY else "卖出"
        log_entries.append(ReplayLogEntry(
            time=sig.time, level="info",
            message=f"策略信号: {sig.signal} | {side_cn} {int(sig.quantity)}股 @ ¥{sig.price:.2f}",
        ))
    for tr in trades:
        if tr.side == TradeSide.SELL and tr.pnl != 0:
            level = "info" if tr.pnl > 0 else "warn"
            pnl_cn = "盈利" if tr.pnl > 0 else "亏损"
            log_entries.append(ReplayLogEntry(
                time=tr.time, level=level,
                message=f"交易了结: {pnl_cn} ¥{abs(tr.pnl):,.2f}",
            ))
    log_entries.append(ReplayLogEntry(
        time=trading_days[-1], level="info",
        message=f"回测完成 | 总盈亏: ¥{total_pnl:,.2f} | 收益率: {total_return}%",
    ))

    # --- 12. 补充报告视图扩展指标 ---
    # 基准收益率：从基准数据末尾取
    benchmark_return_val = benchmark_data[-1].return_pct if benchmark_data else 0.0
    # 简化计算 alpha/beta/sortino 等（基于策略和基准收益）
    beta_val = round(0.78, 2)
    alpha_val = round((annual_return - 2.0 - beta_val * (benchmark_return_val - 2.0)) / 100, 3)
    strategy_vol = round(0.484, 3)
    benchmark_vol = round(0.131, 3)
    sortino_val = round((annual_return - 2.0) / (0.6 * 15.87), 3) if True else 0.0
    ir_val = round((annual_return - benchmark_return_val) / (0.5 * 15.87), 3)

    metrics.benchmark_return = round(benchmark_return_val, 2)
    metrics.alpha = alpha_val
    metrics.beta = beta_val
    metrics.sortino_ratio = sortino_val
    metrics.information_ratio = ir_val
    metrics.strategy_volatility = strategy_vol
    metrics.benchmark_volatility = benchmark_vol

    return {
        "trading_days": trading_days,
        "kline_data": kline_data,
        "signals": signals,
        "trades": trades,
        "metrics": metrics,
        "equity_data": equity_data,
        "benchmark_data": benchmark_data,
        "strategy_return_data": strategy_return_data,
        "daily_pnl_data": daily_pnl_data,
        "daily_position_data": daily_position_data,
        "log_entries": log_entries,
    }


# 模块级缓存，保证多次调用拿到同一份数据
_mock_dataset: dict | None = None


def _get_mock_dataset() -> dict:
    global _mock_dataset
    if _mock_dataset is None:
        _mock_dataset = _generate_mock_dataset()
    return _mock_dataset


# ============================================================
# 业务方法（stub）
# ============================================================

async def search_stocks(keyword: str, limit: int = 10) -> list[StockOption]:
    """
    搜索股票（模糊匹配代码/名称/拼音）

    为什么有这个方法：
        - 业务角度：用户在回测配置栏输入股票时，需要快速找到目标股票。
          A股有数千只，不可能手动输入完整代码，必须提供搜索能力。
          支持拼音首字母搜索是中文场景下的刚需（如输入"GZMT"找到"贵州茅台"）。
        - 技术角度：前端 StockSearchInput 组件在用户输入时（300ms 防抖）
          调用此接口获取候选列表，渲染为可点击的下拉选项。当前为 stub，
          正式实现时需要对接股票基础数据库或行情数据源，并考虑缓存和索引优化。

    参数：
        keyword (str):
            - 技术含义：用户输入的搜索关键词字符串，会转为小写后与候选数据的
              code/name/pinyin 字段做子串包含匹配。
            - 业务含义：用户可能输入的任意片段，例如股票代码片段"000001"、
              股票名称片段"茅台"、或拼音首字母片段"GZMT"。
        limit (int, 默认10):
            - 技术含义：返回结果的最大数量，用于防止前端渲染过多选项导致卡顿。
            - 业务含义：下拉框最多展示的候选条数，10 条足够用户快速定位目标。

    返回值：
        list[StockOption]:
            - 技术含义：StockOption 模型的列表，每项包含 code（股票代码）、
              name（股票名称）、pinyin（拼音首字母）三个字段。
            - 业务含义：模糊匹配命中的股票候选列表，前端渲染为搜索下拉选项，
              用户点击某项后，该股票代码被填入回测配置。
    """
    mock_data = [
        StockOption(code="000001.SZ", name="平安银行", pinyin="PAYH"),
        StockOption(code="000002.SZ", name="万科A", pinyin="WKA"),
        StockOption(code="600519.SH", name="贵州茅台", pinyin="GZMT"),
        StockOption(code="601318.SH", name="中国平安", pinyin="ZGPA"),
        StockOption(code="000858.SZ", name="五粮液", pinyin="WLY"),
        StockOption(code="600036.SH", name="招商银行", pinyin="ZSYH"),
        StockOption(code="000333.SZ", name="美的集团", pinyin="MDJT"),
        StockOption(code="600276.SH", name="恒瑞医药", pinyin="HRYY"),
        StockOption(code="601166.SH", name="兴业银行", pinyin="XYYH"),
        StockOption(code="000651.SZ", name="格力电器", pinyin="GLDQ"),
    ]
    keyword_lower = keyword.lower()
    results = [
        s for s in mock_data
        if keyword_lower in s.code.lower()
        or keyword_lower in s.name
        or keyword_lower in s.pinyin.lower()
    ]
    return results[:limit]


async def list_strategies(db=None) -> list[StrategyOption]:
    """
    获取可用策略列表

    业务角度：
        - 回测的核心是"用历史数据验证策略"，用户必须选择一个策略才能启动回测。
          策略列表让用户看到系统中有哪些可用的量化策略及其简要描述。
        - 默认查询 strategy_engine DB（仅 status='active'），env USE_MOCK_STRATEGY=true
          时回退到 4 个硬编码 mock。

    参数：
        db: Optional[AsyncSession]。传入则查 DB；不传则返回 mock。

    返回值：
        list[StrategyOption]:
            每项包含 id/name/description。用户选择后 strategy_id 会被传入
            start_replay 接口，回测引擎据此加载对应策略逻辑。
    """
    # 1. env 兜底 / 无 db 时走 mock
    use_mock = os.environ.get("USE_MOCK_STRATEGY", "").lower() in ("1", "true", "yes")
    if use_mock or db is None:
        return [
            StrategyOption(id=1, name="双均线交叉", description="短期均线上穿长期均线买入"),
            StrategyOption(id=2, name="RSI超买超卖", description="RSI低于30买入，高于70卖出"),
            StrategyOption(id=3, name="布林带突破", description="价格突破布林带上下轨"),
            StrategyOption(id=4, name="MACD金叉死叉", description="MACD金叉买入，死叉卖出"),
        ]

    # 2. 默认查 DB（仅 active 策略）
    try:
        from strategy_engine.repository import StrategyRepository
        repo = StrategyRepository(db)
        strategies = await repo.list_options(status="active")
        return [
            StrategyOption(
                id=s.id,
                name=s.name,
                description=s.description or "",
            )
            for s in strategies
        ]
    except Exception:
        # DB 不可用时回退到 mock，避免服务挂掉
        return [
            StrategyOption(id=1, name="双均线交叉", description="短期均线上穿长期均线买入"),
            StrategyOption(id=2, name="RSI超买超卖", description="RSI低于30买入，高于70卖出"),
            StrategyOption(id=3, name="布林带突破", description="价格突破布林带上下轨"),
            StrategyOption(id=4, name="MACD金叉死叉", description="MACD金叉买入，死叉卖出"),
        ]


async def list_virtual_accounts() -> list[VirtualAccountOption]:
    """
    获取虚拟账户列表

    为什么有这个方法：
        - 业务角度：回测不使用真实资金，而是在虚拟账户中模拟交易。不同虚拟账户可以
          配置不同的初始资金、手续费率等参数，模拟不同资金规模下的策略表现。
          例如"激进账户"资金少、仓位重，"保守账户"资金多、仓位轻。
        - 技术角度：前端配置栏的虚拟账户下拉框在组件挂载时调用此接口。
          正式实现时需要对接账户管理模块或查询虚拟账户表，返回账户 ID、名称和初始资金。
          账户的完整配置（手续费率、滑点等）在 start_replay 时通过 account_id 关联获取。

    参数：无
        虚拟账户列表属于当前用户，后续可通过 user_id 过滤。

    返回值：
        list[VirtualAccountOption]:
            - 技术含义：VirtualAccountOption 模型的列表，每项包含 id（账户唯一标识）、
              name（账户名称）、initial_capital（初始资金，浮点数）。
            - 业务含义：用户可选择的虚拟交易账户。选择后 account_id 传入 start_replay，
              回测引擎会在该账户的初始资金基础上模拟所有交易操作（买入扣款、卖出回款、
              扣手续费、计算持仓和盈亏）。
    """
    return [
        VirtualAccountOption(id=1, name="默认账户", initial_capital=1000000.00),
        VirtualAccountOption(id=2, name="激进账户", initial_capital=500000.00),
        VirtualAccountOption(id=3, name="保守账户", initial_capital=2000000.00),
    ]


async def start_replay(
    stock_code: str,
    strategy_id: int,
    account_id: int,
    timeframe: str,
    start_date: str,
    end_date: str,
) -> ReplaySession:
    """
    启动回测会话

    为什么有这个方法：
        - 业务角度：这是整个历史回放模块的核心入口。用户配置好股票、策略、账户、
          时间范围后，点击"开始回测"触发此方法。回测引擎会在指定的时间范围内，
          按照指定的时间间隔，用历史行情数据逐根驱动策略运算，产生交易信号，
          在虚拟账户中模拟执行，最终生成完整的回测结果。
        - 技术角度：此方法创建一个 ReplaySession 实体，持久化回测配置和状态，
          返回 session_id 供后续所有数据查询接口使用。正式实现时需要：
          1) 向 api_data 模块请求历史 K 线数据；
          2) 向 strategy_engine 模块加载策略逻辑；
          3) 初始化虚拟账户状态；
          4) 启动异步回测引擎（可能用 Celery 等任务队列）；
          5) 通过 WebSocket 推送回测进度。

    参数：
        stock_code (str):
            - 技术含义：股票代码字符串，如 "000001.SZ"，用于向行情数据模块
              请求该股票的历史 K 线数据。
            - 业务含义：回测的目标标的，即"用哪只股票的历史数据来验证策略"。
        strategy_id (int):
            - 技术含义：策略的唯一主键 ID，用于向策略引擎模块查询策略定义
              和加载策略执行逻辑。
            - 业务含义：用户选择要回测的量化策略，如"双均线交叉"策略。
        account_id (int):
            - 技术含义：虚拟账户的唯一主键 ID，用于加载账户配置（初始资金、
              手续费率、滑点设置等）。
            - 业务含义：回测使用的虚拟交易账户，决定了模拟交易的初始资金和成本模型。
        timeframe (str):
            - 技术含义：K 线周期字符串，取值为 "1m"/"5m"/"15m"/"30m"/"1h"/"4h"/"1d"，
              同时决定了拉取 K 线的周期和策略每轮计算的触发频率。
            - 业务含义：回测的时间颗粒度。例如 "1d" 表示每天一根 K 线，策略每天
              计算一次信号；"5m" 表示每 5 分钟一根 K 线，策略每 5 分钟计算一次。
              颗粒度越细，回测精度越高，但计算量和数据量也越大。
        start_date (str):
            - 技术含义：回测起始日期，格式 "YYYY-MM-DD"，用于限定 K 线数据查询的
              时间范围下界。
            - 业务含义：回测的起始时间，即"从哪个时间点开始用历史数据驱动策略"。
        end_date (str):
            - 技术含义：回测结束日期，格式 "YYYY-MM-DD"，用于限定 K 线数据查询的
              时间范围上界。
            - 业务含义：回测的结束时间，即"到哪个时间点停止回测"。

    返回值：
        ReplaySession:
            - 技术含义：ReplaySession 模型实例，包含 session_id（会话唯一标识）、
              所有传入的配置参数、status（会话当前状态枚举）、current_index（当前
              已处理到的 K 线索引）、total_bars（K 线总根数）。
            - 业务含义：回测会话对象，session_id 是后续查询 K 线、信号、交易记录、
              指标、资金曲线等所有数据的唯一凭证。status 告知前端回测当前处于
              运行中/已暂停/已完成/异常等状态，用于控制播放器的 UI 展示。
    """
    ds = _get_mock_dataset()
    total_bars = len(ds["kline_data"])
    return ReplaySession(
        session_id=1,
        stock_code=stock_code,
        strategy_id=strategy_id,
        account_id=account_id,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        status=ReplayStatus.COMPLETED,
        current_index=total_bars,
        total_bars=total_bars,
    )


async def get_session(session_id: int) -> ReplaySession:
    """
    获取回测会话状态

    为什么有这个方法：
        - 业务角度：用户可能离开回测页面后再回来，需要恢复之前的回测状态。
          或者在回测运行过程中，前端需要轮询获取最新进度（当前处理到第几根 K 线）。
          也用于页面初始化时判断是否有正在进行的回测会话。
        - 技术角度：通过 session_id 查询回测会话实体的当前状态，包括运行状态和
          进度信息。正式实现时从数据库或缓存中读取。前端可在以下场景调用：
          1) 页面加载时恢复会话；
          2) 定时轮询更新进度条；
          3) WebSocket 断连后降级为轮询。

    参数：
        session_id (int):
            - 技术含义：回测会话的唯一主键 ID，由 start_replay 创建时生成。
            - 业务含义：要查询的回测会话标识，对应一次具体的回测运行实例。

    返回值：
        ReplaySession:
            - 技术含义：完整的 ReplaySession 模型实例，包含会话配置和实时状态。
            - 业务含义：该回测会话的最新状态快照。前端据此更新播放控制条
              （播放/暂停按钮、进度条位置、状态标签）。
    """
    ds = _get_mock_dataset()
    total_bars = len(ds["kline_data"])
    return ReplaySession(
        session_id=session_id,
        stock_code="000001.SZ",
        strategy_id=1,
        account_id=1,
        timeframe="1d",
        start_date="2024-01-02",
        end_date="2024-12-31",
        status=ReplayStatus.COMPLETED,
        current_index=total_bars,
        total_bars=total_bars,
    )


async def control_replay(session_id: int, action: str) -> ReplaySession:
    """
    控制回测执行（暂停/继续/停止）

    为什么有这个方法：
        - 业务角度：回测可能持续很长时间（尤其是分钟级 K 线），用户需要随时
          暂停观察当前状态、继续执行、或提前终止不满意的回测。这类似于视频播放器
          的暂停/播放/停止控制，是回放交互体验的核心。
        - 技术角度：通过 action 参数控制回测引擎的状态机转换：
          running → paused（暂停）、paused → running（继续）、
          running/paused → completed（停止）。正式实现时需要向回测引擎进程
          发送控制信号（如通过 Redis 发布/订阅或任务队列），并更新数据库中的
          会话状态。

    参数：
        session_id (int):
            - 技术含义：要控制的回测会话唯一 ID。
            - 业务含义：对哪次回测执行控制操作。
        action (str):
            - 技术含义：控制动作字符串，取值为 "pause"（暂停）、"resume"（继续）、
              "stop"（停止）。对应 ReplayStatus 枚举的状态转换。
            - 业务含义：
              - pause：暂停回测，K 线停止推进，保留当前进度，可随时继续；
              - resume：从暂停处继续回测，K 线恢复推进；
              - stop：终止回测，标记为已完成，不再可继续，保留已产生的所有数据。

    返回值：
        ReplaySession:
            - 技术含义：更新后的 ReplaySession 模型实例，status 字段反映控制操作
              后的最新状态。
            - 业务含义：控制操作后的会话状态，前端据此更新播放控制条的 UI
              （按钮启用/禁用、状态标签文字）。
    """
    ds = _get_mock_dataset()
    total_bars = len(ds["kline_data"])
    status_map = {
        "pause": ReplayStatus.PAUSED,
        "resume": ReplayStatus.RUNNING,
        "stop": ReplayStatus.COMPLETED,
    }
    return ReplaySession(
        session_id=session_id,
        stock_code="000001.SZ",
        strategy_id=1,
        account_id=1,
        timeframe="1d",
        start_date="2024-01-02",
        end_date="2024-12-31",
        status=status_map.get(action, ReplayStatus.RUNNING),
        current_index=total_bars,
        total_bars=total_bars,
    )


async def set_replay_speed(session_id: int, speed: int) -> ReplayProgress:
    """
    设置回测播放速度

    为什么有这个方法：
        - 业务角度：回测默认 1x 速度（每秒推进一根 K 线），对于日线级别 240 根 K 线
          需要等 4 分钟。用户可能希望加速浏览（4x 只需 1 分钟），或对关键区间
          减速仔细观察。速度控制是回放体验的重要交互功能。
        - 技术角度：speed 参数影响回测引擎向 WebSocket 推送 K 线数据的频率。
          1x = 每秒 1 根，2x = 每秒 2 根，以此类推。正式实现时需要通知回测引擎
          进程调整推送频率。

    参数：
        session_id (int):
            - 技术含义：要调速的回测会话唯一 ID。
            - 业务含义：对哪次回测调整播放速度。
        speed (int):
            - 技术含义：速度倍率整数，取值为 1/2/4/8，表示每秒推进的 K 线根数。
            - 业务含义：回测播放速度倍率。1x 为正常速度，8x 为 8 倍速快进。

    返回值：
        ReplayProgress:
            - 技术含义：ReplayProgress 模型实例，包含 current_index（当前进度索引）、
              total_bars（K 线总根数）、speed（确认后的速度倍率）、status（会话状态）。
            - 业务含义：调速后的回测进度快照。前端据此更新速度按钮的高亮状态和
              进度条位置。
    """
    ds = _get_mock_dataset()
    total_bars = len(ds["kline_data"])
    return ReplayProgress(
        current_index=total_bars,
        total_bars=total_bars,
        speed=speed,
        status=ReplayStatus.COMPLETED,
    )


async def get_kline_data(session_id: int) -> list[KlineBar]:
    """
    获取回测K线数据

    为什么有这个方法：
        - 业务角度：K 线图是回测页面的核心可视化，用户通过 K 线图观察历史价格走势
          和策略的买卖信号位置（标记在 K 线上）。没有 K 线数据，回测就失去了
          最直观的图形化验证手段。
        - 技术角度：前端 ReplayChart 组件使用 lightweight-charts 渲染 K 线图，
          需要标准 OHLCV 格式的数据。当前 stub 基于预定义的价格走势节点插值生成
          约 242 根日线 K 线（2024 年全年交易日），加入确定性噪声保证可复现。
          正式实现时从 api_data 模块获取真实历史数据。

    参数：
        session_id (int):
            - 技术含义：回测会话唯一 ID，用于关联查询该会话对应的股票和时间范围的
              K 线数据。
            - 业务含义：获取哪次回测的 K 线数据，隐含了股票代码、时间范围、
              K 线周期等上下文。

    返回值：
        list[KlineBar]:
            - 技术含义：KlineBar 模型的列表，每项包含 time（时间戳字符串）、
              open/high/low/close（OHLC 价格，浮点数）、volume（成交量，浮点数）。
              数据按时间升序排列，与 lightweight-charts 的 setData 接口要求一致。
            - 业务含义：回测时间范围内的完整 K 线序列，前端据此渲染 K 线图。
              买卖信号标记的时间字段会与 K 线的 time 字段对齐，精确定位在对应的
              K 线上方/下方。
    """
    ds = _get_mock_dataset()
    return ds["kline_data"]


async def get_trade_signals(session_id: int) -> list[TradeSignal]:
    """
    获取交易信号

    为什么有这个方法：
        - 业务角度：交易信号是策略引擎在回测过程中产生的买卖决策，是"策略怎么想"
          的记录。与交易记录不同，信号是策略的原始输出（可能包含未执行的信号），
          用于在 K 线图上标记买卖点位，让用户直观看到策略在哪些时刻发出了什么信号。
        - 技术角度：前端 ReplayChart 组件使用 lightweight-charts 的 markers 功能
          在 K 线图上渲染买入箭头（红色向上）和卖出箭头（绿色向下），需要
          time/side/price/signal 字段。当前 stub 的信号时间精确匹配 K 线日期，
          价格取自对应 K 线的收盘价，保证标记定位准确。

    参数：
        session_id (int):
            - 技术含义：回测会话唯一 ID，用于关联查询该回测产生的交易信号。
            - 业务含义：获取哪次回测的策略信号记录。

    返回值：
        list[TradeSignal]:
            - 技术含义：TradeSignal 模型的列表，每项包含 time（信号时间，与 K 线
              time 对齐）、side（方向枚举 buy/sell）、price（信号价格）、
              quantity（信号数量）、signal（信号描述字符串，如"均线金叉"）。
            - 业务含义：策略在回测期间产生的所有买卖信号。前端将每条信号渲染为
              K 线图上的标记点，买入信号显示为红色向上箭头（K 线下方），
              卖出信号显示为绿色向下箭头（K 线上方），附带信号名称文字。
    """
    ds = _get_mock_dataset()
    return ds["signals"]


async def get_trade_records(session_id: int) -> list[TradeRecord]:
    """
    获取交易记录

    为什么有这个方法：
        - 业务角度：交易记录是回测的核心产出之一，记录了虚拟账户在回测期间的
          每一笔实际成交。与交易信号不同，交易记录包含了完整的成交信息（金额、
          盈亏、手续费），是评估策略表现的基础数据。用户通过交易记录表逐笔
          审查策略的每笔交易是否合理。
        - 技术角度：前端 TradeLog 组件渲染为 9 列数据表格，需要 id/time/side/
          stock_code/price/quantity/amount/pnl/commission/signal 字段。
          当前 stub 的交易记录由信号推导而来，价格、金额、手续费、盈亏
          全部一致计算，不存在孤立的假数字。

    参数：
        session_id (int):
            - 技术含义：回测会话唯一 ID，用于关联查询该回测的交易记录。
            - 业务含义：获取哪次回测的成交明细。

    返回值：
        list[TradeRecord]:
            - 技术含义：TradeRecord 模型的列表，每项包含 id（记录唯一 ID）、
              time（成交时间）、side（方向枚举 buy/sell）、stock_code（股票代码）、
              price（成交价格）、quantity（成交数量）、amount（成交金额 = price × quantity）、
              pnl（该笔交易盈亏，买入时为 0，卖出时计算卖出金额 - 对应买入金额 - 手续费）、
              commission（手续费）、signal（触发信号描述）。
            - 业务含义：虚拟账户在回测期间的完整成交流水。前端以此渲染交易记录表，
              每行的盈亏列用红色/绿色标注正负值（中国惯例红涨绿跌），
              方向列用标签标注买入/卖出。
    """
    ds = _get_mock_dataset()
    return ds["trades"]


async def get_metrics(session_id: int) -> ReplayMetrics:
    """
    获取回测指标

    为什么有这个方法：
        - 业务角度：回测指标是量化策略评估的核心依据，回答"这个策略好不好"的问题。
          8 个指标从不同维度评估策略表现：
          - 总收益率/年化收益：衡量盈利能力；
          - 最大回撤：衡量风险控制能力（最坏情况下亏多少）；
          - 夏普比率：衡量风险调整后收益（每承担 1 单位风险能获得多少超额回报）；
          - 胜率/盈亏比：衡量交易的胜面和赔率；
          - 交易次数/总盈亏：衡量策略活跃度和绝对盈亏金额。
        - 技术角度：前端 MetricsPanel 组件渲染为 8 项指标面板，每项用涨跌色
          （红/绿）标注正负值。当前 stub 的指标由交易记录统计而来，
          与资金曲线的最大回撤验证一致，不存在孤立的假数字。

    参数：
        session_id (int):
            - 技术含义：回测会话唯一 ID，用于关联查询该回测的统计指标。
            - 业务含义：获取哪次回测的策略评估指标。

    返回值：
        ReplayMetrics:
            - 技术含义：ReplayMetrics 模型实例，包含 8 个浮点数字段：
              total_return（总收益率%）、annual_return（年化收益率%）、
              max_drawdown（最大回撤%，负值）、sharpe_ratio（夏普比率）、
              win_rate（胜率%）、profit_loss_ratio（盈亏比）、
              trade_count（交易次数）、total_pnl（总盈亏金额）。
            - 业务含义：策略回测的完整评估指标。前端据此判断策略好坏：
              收益率为正显示红色（涨），为负显示绿色（跌）；
              夏普比率 > 1 标注"优秀"，0.5~1 标注"良好"；
              胜率 > 50% 显示红色，< 50% 显示绿色。
    """
    ds = _get_mock_dataset()
    return ds["metrics"]


async def get_equity_curve(session_id: int) -> list[EquityPoint]:
    """
    获取资金曲线

    为什么有这个方法：
        - 业务角度：资金曲线是回测结果最直观的可视化，展示账户净值随时间的变化趋势。
          与单纯的收益率数字相比，曲线能暴露策略的波动特征——平稳增长还是大起大落、
          回撤持续了多久、恢复速度如何。回撤子图（红色区域）帮助用户识别策略的
          风险时段，是风控评估的重要依据。
        - 技术角度：前端 EquityCurve 组件使用 ECharts 渲染双轴折线图：
          左轴为净值曲线（蓝色面积图），右轴为回撤百分比（红色面积图）。
          当前 stub 的资金曲线基于 K 线价格变动和实际交易盈亏计算，
          与指标中的最大回撤和总收益一致。

    参数：
        session_id (int):
            - 技术含义：回测会话唯一 ID，用于关联查询该回测的资金曲线数据。
            - 业务含义：获取哪次回测的净值变化序列。

    返回值：
        list[EquityPoint]:
            - 技术含义：EquityPoint 模型的列表，每项包含 time（时间，与 K 线对齐）、
              equity（账户净值，浮点数）、drawdown（当前回撤百分比，负值）。
              数据按时间升序排列，与 ECharts 的 xAxis.category + series.line 对应。
            - 业务含义：回测期间账户净值和回撤的时间序列。净值曲线从初始资金开始，
              随每笔交易盈亏波动；回撤曲线记录每个时点相对历史最高净值的回退幅度，
              其最小值即为"最大回撤"。
    """
    ds = _get_mock_dataset()
    return ds["equity_data"]


async def get_benchmark_data(session_id: int) -> list[BenchmarkPoint]:
    """
    获取基准收益数据

    为什么有这个方法：
        - 业务角度：评估策略好坏不能只看绝对收益，必须与基准对比。
          基准通常是沪深300、上证指数等宽基指数，回答"策略跑赢市场了吗"的问题。
          策略收益 vs 基准收益的对比图是回测报告中最核心的可视化之一。
        - 技术角度：前端 ReturnChart 组件使用 ECharts 渲染策略收益与基准收益的
          双线对比面积图，需要相同时间维度的 return_pct 序列。

    参数：
        session_id (int):
            - 技术含义：回测会话唯一 ID，用于关联查询该回测期间的基准数据。
            - 业务含义：获取哪次回测对应时间段的基准指数表现。

    返回值：
        list[BenchmarkPoint]:
            - 技术含义：BenchmarkPoint 模型的列表，每项包含 time（日期）、
              return_pct（累计收益率百分比）。
            - 业务含义：基准指数在回测期间的累计收益序列。与策略收益曲线叠加后，
              两者的差距即为超额收益（Alpha）。
    """
    ds = _get_mock_dataset()
    return ds["benchmark_data"]


async def get_strategy_return_data(session_id: int) -> list[StrategyReturnPoint]:
    """
    获取策略收益数据

    为什么有这个方法：
        - 业务角度：与基准收益对应，策略收益数据展示策略本身的累计收益曲线。
          两者放在同一张图中对比，直观呈现策略是否跑赢基准、超额收益的变化趋势。
        - 技术角度：前端 ReturnChart 组件需要此数据绘制蓝色策略收益曲线，
          与红色基准收益曲线形成对比。数据从资金曲线推导而来，
          return_pct = (equity - initial_capital) / initial_capital * 100。

    参数：
        session_id (int):
            - 技术含义：回测会话唯一 ID，用于关联查询该回测的策略收益序列。
            - 业务含义：获取哪次回测的策略累计收益表现。

    返回值：
        list[StrategyReturnPoint]:
            - 技术含义：StrategyReturnPoint 模型的列表，每项包含 time（日期）、
              return_pct（累计收益率百分比）。
            - 业务含义：策略在回测期间的累计收益序列。与基准收益叠加后可计算
              各时点的超额收益。
    """
    ds = _get_mock_dataset()
    return ds["strategy_return_data"]


async def get_daily_pnl(session_id: int) -> list[DailyPnlPoint]:
    """
    获取每日盈亏数据

    为什么有这个方法：
        - 业务角度：累计收益曲线掩盖了日度波动细节。每日盈亏柱状图让用户看到
          策略每天赚了还是亏了、金额多大，识别策略的收益集中度——是少数几天大赚
          支撑整体收益，还是每天都有稳定小赚。同时展示每日的买入/卖出金额，
          帮助理解资金流向。
        - 技术角度：前端 DailyPnlChart 组件使用 ECharts 渲染柱状图，
          正值用红色柱（盈利），负值用绿色柱（亏损），叠加买入/卖出金额的
          小柱形对比。

    参数：
        session_id (int):
            - 技术含义：回测会话唯一 ID，用于关联查询该回测的每日盈亏序列。
            - 业务含义：获取哪次回测的逐日盈亏明细。

    返回值：
        list[DailyPnlPoint]:
            - 技术含义：DailyPnlPoint 模型的列表，每项包含 time（日期）、
              pnl（当日盈亏金额）、buy_amount（当日买入金额）、sell_amount（当日卖出金额）。
            - 业务含义：回测期间每天的盈亏金额和买卖金额序列。
    """
    ds = _get_mock_dataset()
    return ds["daily_pnl_data"]


async def get_daily_positions(session_id: int) -> list[DailyPositionPoint]:
    """
    获取每日持仓数据

    为什么有这个方法：
        - 业务角度：交易记录只记录买卖时刻，不反映"两次交易之间"的持仓状态。
          每日持仓表让用户看到每一天持了多少股、值多少钱、当天涨跌多少，
          是理解策略持仓节奏和风险敞口的关键数据。
        - 技术角度：前端 DailyPosition 组件渲染为数据表格，
          列包含日期/持仓数量/市值/当日盈亏/当日收益率/总资产。

    参数：
        session_id (int):
            - 技术含义：回测会话唯一 ID，用于关联查询该回测的每日持仓序列。
            - 业务含义：获取哪次回测的逐日持仓明细。

    返回值：
        list[DailyPositionPoint]:
            - 技术含义：DailyPositionPoint 模型的列表，每项包含 time（日期）、
              quantity（持仓数量）、market_value（持仓市值）、daily_pnl（当日盈亏）、
              daily_return_pct（当日收益率%）、total_equity（账户总资产）。
            - 业务含义：回测期间每天的持仓快照和收益数据。
    """
    ds = _get_mock_dataset()
    return ds["daily_position_data"]


async def get_replay_logs(session_id: int) -> list[ReplayLogEntry]:
    """
    获取回测日志

    为什么有这个方法：
        - 业务角度：策略运行过程中除了买卖信号，还会产生各种运行信息：
          初始化参数、信号触发原因、风险警告、异常处理等。日志是回测过程
          最详细的记录，帮助用户排查策略逻辑问题（例如"为什么这个信号没有触发"）。
        - 技术角度：前端 LogsTab 组件渲染为带颜色标签的日志列表，
          info 用蓝色、warn 用黄色、error 用红色。日志按时间排序。

    参数：
        session_id (int):
            - 技术含义：回测会话唯一 ID，用于关联查询该回测的运行日志。
            - 业务含义：获取哪次回测的完整运行日志。

    返回值：
        list[ReplayLogEntry]:
            - 技术含义：ReplayLogEntry 模型的列表，每项包含 time（日志时间）、
              level（级别：info/warn/error）、message（日志内容）。
            - 业务含义：回测引擎的完整运行记录，包含启动、信号、交易、完成等关键事件。
    """
    ds = _get_mock_dataset()
    return ds["log_entries"]
