"""内置策略 1：双均线交叉。

策略逻辑：
- 短期均线（默认 5 日）上穿长期均线（默认 10 日） → 全仓买入
- 短期均线下穿长期均线 → 全部卖出

参数：
- short_window: 短期均线周期（默认 5）
- long_window: 长期均线周期（默认 10）
- buy_ratio: 买入仓位比例（默认 0.95，留 5% 现金应对手续费）

特点：经典趋势跟随策略，在趋势行情中表现好，震荡行情中频繁假信号。
"""

STRATEGY_CODE = """
# 双均线交叉策略
# 参数通过 g 注入：g.short_window / g.long_window / g.buy_ratio

def initialize(context):
    if not hasattr(g, 'short_window'):
        g.short_window = 5
    if not hasattr(g, 'long_window'):
        g.long_window = 10
    if not hasattr(g, 'buy_ratio'):
        g.buy_ratio = 0.95
    g.last_above = False  # 上一轮 short 是否在 long 之上


def handle_data(context, data):
    security = context.universe[0] if context.universe else None
    if not security:
        return

    # 取最近 long_window+5 根 bar，确保能算出两条均线
    df = get_history(g.long_window + 5, '1d', 'close', security, fq='qfq', include=True)
    if not df or 'close' not in df or len(df['close']) < g.long_window:
        return

    closes = df['close']
    short_ma = sum(closes[-g.short_window:]) / g.short_window
    long_ma = sum(closes[-g.long_window:]) / g.long_window
    above = short_ma > long_ma

    # 金叉：上一轮 short 在 long 之下，本轮 short 在 long 之上
    golden_cross = above and not g.last_above
    # 死叉：上一轮 short 在 long 之上，本轮 short 在 long 之下
    death_cross = (not above) and g.last_above

    if golden_cross:
        # 全仓买入（按 buy_ratio 比例）
        buy_value = context.portfolio.cash * g.buy_ratio
        if buy_value > 0:
            order_value(security, buy_value)
            log.info('金叉买入 short_ma=%.2f long_ma=%.2f' % (short_ma, long_ma))

    elif death_cross:
        # 全部卖出
        pos = context.portfolio.positions.get(security)
        if pos and pos.quantity > 0:
            order_target(security, 0)
            log.info('死叉卖出 short_ma=%.2f long_ma=%.2f' % (short_ma, long_ma))

    g.last_above = above


def control_risk(context):
    # 单日最大亏损 5%（P1 简化：仅记录，不平仓）
    pass
"""
