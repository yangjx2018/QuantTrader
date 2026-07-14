"""内置策略 3：布林带突破。

策略逻辑：
- 价格突破上轨 → 趋势启动，买入
- 价格跌破下轨 → 趋势反转，卖出
- 或：价格回归中轨时平仓（均值回归版）

参数：
- period: 布林带计算周期（默认 20）
- std_dev: 标准差倍数（默认 2，即上下轨距中轨 2σ）

特点：突破策略，在低波动期后启动的趋势中表现好。
"""

STRATEGY_CODE = """
# 布林带突破策略
# 参数通过 g 注入：g.period / g.std_dev / g.buy_ratio

def initialize(context):
    if not hasattr(g, 'period'):
        g.period = 20
    if not hasattr(g, 'std_dev'):
        g.std_dev = 2.0
    if not hasattr(g, 'buy_ratio'):
        g.buy_ratio = 0.9


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _std(values):
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return var ** 0.5


def handle_data(context, data):
    security = context.universe[0] if context.universe else None
    if not security:
        return

    df = get_history(g.period + 5, '1d', 'close', security, fq='qfq', include=True)
    if not df or 'close' not in df or len(df['close']) < g.period:
        return

    closes = df['close']
    window = closes[-g.period:]
    middle = _mean(window)
    sigma = _std(window)
    upper = middle + g.std_dev * sigma
    lower = middle - g.std_dev * sigma

    price = data[security]['close']

    if price > upper:
        # 突破上轨买入
        buy_value = context.portfolio.cash * g.buy_ratio
        if buy_value > 0:
            order_value(security, buy_value)
            log.info('突破上轨买入 price=%.2f upper=%.2f' % (price, upper))

    elif price < lower:
        # 跌破下轨卖出
        pos = context.portfolio.positions.get(security)
        if pos and pos.quantity > 0:
            order_target(security, 0)
            log.info('跌破下轨卖出 price=%.2f lower=%.2f' % (price, lower))

    # 价格回归中轨时也可平仓（保守版本，默认注释）
    # elif price > middle * 0.99 and price < middle * 1.01:
    #     pos = context.portfolio.positions.get(security)
    #     if pos and pos.quantity > 0:
    #         order_target(security, 0)


def control_risk(context):
    pass
"""
