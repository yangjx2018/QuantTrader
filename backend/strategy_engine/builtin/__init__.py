"""内置策略集合。

每个文件导出常量 STRATEGY_CODE（Python 源码字符串），用于：
1. 数据库迁移 INSERT 到 strategy.code_content
2. 单元测试加载
3. 文档参考

策略代码规则（与 strategy-dsl-runtime spec 一致）：
- 必须定义 initialize(context) 与 handle_data(context, data)
- 可选定义 before_trading_start(context) / control_risk(context)
- 允许使用的全局：g / log / context / data / order / order_value / order_target /
                  order_target_value / get_history / set_universe / get_current_data
- 允许 import：math / statistics / datetime / decimal / json / collections
- 禁用：open / eval / exec / __import__ / os / sys 等
"""

from strategy_engine.builtin.double_ma import STRATEGY_CODE as DOUBLE_MA_CODE
from strategy_engine.builtin.rsi import STRATEGY_CODE as RSI_CODE
from strategy_engine.builtin.bollinger import STRATEGY_CODE as BOLLINGER_CODE
from strategy_engine.builtin.macd import STRATEGY_CODE as MACD_CODE

__all__ = [
    "DOUBLE_MA_CODE",
    "RSI_CODE",
    "BOLLINGER_CODE",
    "MACD_CODE",
]
