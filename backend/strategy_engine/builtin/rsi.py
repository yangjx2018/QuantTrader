"""内置策略 2：RSI 超买超卖反转。

策略逻辑：
- RSI 低于超卖线（默认 30） → 买入（认为超跌反弹）
- RSI 高于超买线（默认 70） → 卖出（认为超涨回落）

参数：
- period: RSI 计算周期（默认 14）
- oversold: 超卖阈值（默认 30）
- overbought: 超买阈值（默认 70）
- buy_ratio: 买入仓位比例（默认 0.5，RSI 反转风险高，半仓介入）

特点：均值回归策略，适合震荡行情；趋势行情中会频繁止损。
"""

STRATEGY_CODE = """
# RSI 超买超卖策略
# 参数通过 g 注入：g.period / g.oversold / g.overbought / g.buy_ratio

def initialize(context):
    if not hasattr(g, 'period'):
        g.period = 14
    if not hasattr(g, 'oversold'):
        g.oversold = 30
    if not hasattr(g, 'overbought'):
        g.overbought = 70
    if not hasattr(g, 'buy_ratio'):
        g.buy_ratio = 0.5


def _calc_rsi(closes):
    '''计算 RSI 指标（简化版，不依赖外部库）。'''
    n = len(closes)
    if n < 2:
        return 50.0
    gains = []
    losses = []
    for i in range(1, n):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def handle_data(context, data):
    security = context.universe[0] if context.universe else None
    if not security:
        return

    df = get_history(g.period + 5, '1d', 'close', security, fq='qfq', include=True)
    if not df or 'close' not in df or len(df['close']) < g.period + 1:
        return

    closes = df['close']
    rsi = _calc_rsi(closes[-g.period-1:])

    if rsi < g.oversold:
        # 超卖买入
        buy_value = context.portfolio.cash * g.buy_ratio
        if buy_value > 0:
            order_value(security, buy_value)
            log.info('RSI=%.1f 超卖买入' % rsi)

    elif rsi > g.overbought:
        # 超买卖出
        pos = context.portfolio.positions.get(security)
        if pos and pos.quantity > 0:
            order_target(security, 0)
            log.info('RSI=%.1f 超买卖出' % rsi)


def control_risk(context):
    pass
"""
