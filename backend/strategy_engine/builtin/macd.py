"""内置策略 4：MACD 金叉死叉。

策略逻辑：
- MACD 柱状图（MACD - Signal）从负转正 → 金叉买入
- MACD 柱状图从正转负 → 死叉卖出

参数：
- fast: 快线 EMA 周期（默认 12）
- slow: 慢线 EMA 周期（默认 26）
- signal: 信号线 EMA 周期（默认 9）
- buy_ratio: 买入仓位比例（默认 0.9）

特点：经典趋势指标，金叉死叉略滞后于顶底；适合中长周期。
"""

STRATEGY_CODE = """
# MACD 金叉死叉策略
# 参数通过 g 注入：g.fast / g.slow / g.signal / g.buy_ratio

def initialize(context):
    if not hasattr(g, 'fast'):
        g.fast = 12
    if not hasattr(g, 'slow'):
        g.slow = 26
    if not hasattr(g, 'signal'):
        g.signal = 9
    if not hasattr(g, 'buy_ratio'):
        g.buy_ratio = 0.9
    g.last_hist = None  # 上一轮 MACD 柱状图


def _ema(values, period):
    '''指数移动平均。'''
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    # 初始 SMA 作为种子
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def handle_data(context, data):
    security = context.universe[0] if context.universe else None
    if not security:
        return

    # 取 slow + signal + buffer 根 bar
    n_needed = g.slow + g.signal + 5
    df = get_history(n_needed, '1d', 'close', security, fq='qfq', include=True)
    if not df or 'close' not in df or len(df['close']) < g.slow + g.signal:
        return

    closes = df['close']

    # 算 fast_ema / slow_ema 序列
    fast_series = []
    slow_series = []
    for end in range(g.slow, len(closes) + 1):
        window = closes[:end]
        f = _ema(window, g.fast)
        s = _ema(window, g.slow)
        if f is not None and s is not None:
            fast_series.append(f)
            slow_series.append(s)

    if len(fast_series) < g.signal + 1:
        return

    # 计算 DIF = fast - slow
    dif = [f - s for f, s in zip(fast_series, slow_series)]
    # signal = EMA(DIF, signal_period)
    signal_line = _ema(dif, g.signal)
    if signal_line is None:
        return
    macd = dif[-1]
    hist = macd - signal_line

    # 金叉：上一轮 hist<0，本轮 hist>0
    if g.last_hist is not None and g.last_hist < 0 and hist > 0:
        buy_value = context.portfolio.cash * g.buy_ratio
        if buy_value > 0:
            order_value(security, buy_value)
            log.info('MACD 金叉买入 hist=%.4f' % hist)

    # 死叉：上一轮 hist>0，本轮 hist<0
    elif g.last_hist is not None and g.last_hist > 0 and hist < 0:
        pos = context.portfolio.positions.get(security)
        if pos and pos.quantity > 0:
            order_target(security, 0)
            log.info('MACD 死叉卖出 hist=%.4f' % hist)

    g.last_hist = hist


def control_risk(context):
    pass
"""
