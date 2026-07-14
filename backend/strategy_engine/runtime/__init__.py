"""策略引擎运行时（DSL 加载、沙箱、上下文、内置 API、回测引擎）。

模块布局：
- context.py: Portfolio / Position / StrategyContext 数据结构
- sandbox.py: 受限 globals 构建 + import/builtin 白名单
- api.py: DSL 注入策略代码的内置 API（get_history / order_* / log）
- loader.py: exec 用户代码 + 钩子调度
- engine.py: 回测主循环
"""

from strategy_engine.runtime.context import (
    Position,
    Portfolio,
    StrategyContext,
)
from strategy_engine.runtime.loader import (
    StrategyInstance,
    StrategyLoader,
)
from strategy_engine.runtime.sandbox import (
    ALLOWED_IMPORTS,
    FORBIDDEN_BUILTINS,
)
from strategy_engine.runtime.engine import (
    AccountConfig,
    BacktestEngine,
    BacktestError,
    DataUnavailableError,
    KBar,
    StrategyNotActive,
)
from strategy_engine.runtime.types import (
    BacktestResult,
    BarRecord,
    OrderRecord,
    PositionRecord,
)

__all__ = [
    # 上下文
    "Position",
    "Portfolio",
    "StrategyContext",
    # 加载
    "StrategyInstance",
    "StrategyLoader",
    # 沙箱
    "ALLOWED_IMPORTS",
    "FORBIDDEN_BUILTINS",
    # 引擎
    "BacktestEngine",
    "BacktestError",
    "StrategyNotActive",
    "DataUnavailableError",
    "KBar",
    "AccountConfig",
    # 契约类型
    "BacktestResult",
    "BarRecord",
    "OrderRecord",
    "PositionRecord",
]
