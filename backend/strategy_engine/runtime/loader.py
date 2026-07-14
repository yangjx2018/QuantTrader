"""策略加载器：exec 用户代码 + 钩子调度 + 异常翻译。

设计要点：
- 通过 sandbox.build_safe_globals() 构造受限 globals
- 注入 DSL API（g / log / order_* / get_history / set_universe / get_current_data）
- exec 用户代码，捕获 SyntaxError/RuntimeError，翻译为 StrategyLoadError
- 校验必需钩子 initialize / handle_data 存在
- 提供 has_hook() / call() 方法供引擎调度

钩子调用规则：
- initialize(context)    ：整个回测周期调用 1 次（必须存在）
- before_trading_start(context) ：每 bar 求值前调用（可选）
- handle_data(context, data) ：每 bar 主调用（必须存在）
- control_risk(context)  ：每 bar 撮合后调用（可选）
"""

from __future__ import annotations

import signal
import threading
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Optional

from strategy_engine.exceptions import (
    InvalidStrategyError,
    StrategyLoadError,
    StrategyTimeoutError,
)
from strategy_engine.runtime.api import (
    DSLLogger,
    make_get_current_data,
    make_get_history,
    make_order_api,
    make_set_universe,
)
from strategy_engine.runtime.context import StrategyContext
from strategy_engine.runtime.sandbox import build_safe_globals


# ============================================================
# 异常（统一从 strategy_engine.exceptions 导入，runtime 不再重复定义）
# ============================================================

__all____ = [
    "StrategyInstance",
    "StrategyLoader",
    "StrategyLoadError",
    "InvalidStrategyError",
    "StrategyTimeoutError",
    "validate_code",
    "REQUIRED_HOOKS",
    "OPTIONAL_HOOKS",
]


# ============================================================
# 加载与实例
# ============================================================

# 钩子超时（秒）
DEFAULT_HOOK_TIMEOUT: int = 5

# 必需钩子
REQUIRED_HOOKS: tuple[str, ...] = ("initialize", "handle_data")

# 可选钩子
OPTIONAL_HOOKS: tuple[str, ...] = ("before_trading_start", "control_risk")


@dataclass
class StrategyInstance:
    """exec 后的策略实例。

    封装用户代码中提取出的钩子函数，提供 has_hook / call 方法。
    """

    globals_dict: dict[str, Any]
    parameters: dict[str, Any] = field(default_factory=dict)

    def has_hook(self, name: str) -> bool:
        return callable(self.globals_dict.get(name))

    def call(self, name: str, *args, timeout: int = DEFAULT_HOOK_TIMEOUT) -> Any:
        """同步调用钩子，带超时保护。

        Raises:
            StrategyTimeoutError: 钩子执行超过 timeout 秒
            Exception: 用户代码原始异常（由调用方包装）
        """
        hook = self.globals_dict.get(name)
        if not callable(hook):
            return None
        return _call_with_timeout(hook, args, timeout)


def _call_with_timeout(func: Callable, args: tuple, timeout: int) -> Any:
    """线程 + signal 双重超时保护。

    signal.alarm 仅在主线程生效；线程内执行用 threading.Timer 兜底。
    """
    if threading.current_thread() is threading.main_thread():
        old_handler = signal.getsignal(signal.SIGALRM)
        try:
            signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(StrategyTimeoutError(
                f"hook timeout after {timeout}s"
            )))
            signal.alarm(timeout)
            return func(*args)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    else:
        # 非主线程：降级为无超时
        return func(*args)


class StrategyLoader:
    """策略加载器。

    用法：
        loader = StrategyLoader()
        instance = loader.load(code_content, parameters, context, log, history_loader)
        instance.call("initialize", context)
    """

    def load(
        self,
        code_content: str,
        parameters: Optional[dict[str, Any]] = None,
        context: Optional[StrategyContext] = None,
        log_sink: Optional[list[str]] = None,
        history_loader: Optional[Callable] = None,
    ) -> StrategyInstance:
        """加载并 exec 策略代码。

        Args:
            code_content: Python 源码字符串
            parameters: 策略参数（注入到策略 globals['g'] 的属性，便于 initialize 读取）
            context: 引擎提供的策略上下文（用于 order_* 等闭包绑定）
            log_sink: 引擎提供的日志收集 list
            history_loader: get_history 的实际数据加载回调

        Returns:
            StrategyInstance，含 has_hook / call 方法

        Raises:
            StrategyLoadError: 语法错误 / import 拦截
            InvalidStrategyError: 缺必需钩子
        """
        if not code_content or not code_content.strip():
            raise StrategyLoadError("策略代码为空")

        # 构造 DSL 注入
        log = DSLLogger(sink=log_sink if log_sink is not None else [])
        injects: dict[str, Any] = {
            "g": SimpleNamespace(),
            "log": log,
        }
        if context is not None:
            injects.update(make_order_api(context, log))
            if history_loader is not None:
                injects["get_history"] = make_get_history(history_loader, context)
            injects["set_universe"] = make_set_universe(context)
            injects["get_current_data"] = make_get_current_data(context)

        safe_globals = build_safe_globals(injects)

        # 注入参数到 g
        if parameters:
            for k, v in parameters.items():
                setattr(injects["g"], k, v)

        # exec
        try:
            exec(compile(code_content, "<strategy>", "exec"), safe_globals)
        except SyntaxError as e:
            raise StrategyLoadError(
                f"语法错误 (line {e.lineno}): {e.msg}",
                line=e.lineno,
                original=e,
            ) from e
        except Exception as e:
            # ImportError / NameError / 其他
            raise StrategyLoadError(
                f"加载失败: {type(e).__name__}: {e}",
                original=e,
            ) from e

        # 校验必需钩子
        for hook in REQUIRED_HOOKS:
            if not callable(safe_globals.get(hook)):
                raise InvalidStrategyError(f"缺少必要钩子 {hook}")

        return StrategyInstance(globals_dict=safe_globals, parameters=parameters or {})


# ============================================================
# 工具：仅校验不执行（供 /api/strategy/validate 使用）
# ============================================================

def validate_code(code_content: str) -> dict[str, Any]:
    """静态校验策略代码：语法 + 沙箱可加载性。

    Returns:
        {
            "valid": bool,
            "errors": [{line, severity, code, message}, ...],
            "warnings": [...]
        }
    """
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    # 1. 语法检查（py_compile）
    try:
        compile(code_content, "<strategy>", "exec")
    except SyntaxError as e:
        errors.append({
            "line": e.lineno,
            "column": e.offset,
            "severity": "error",
            "code": "SYNTAX_ERROR",
            "message": e.msg,
        })
        return {"valid": False, "errors": errors, "warnings": warnings}

    # 2. 沙箱加载检查（不绑定 context，只测能否 exec）
    loader = StrategyLoader()
    try:
        loader.load(code_content)
    except InvalidStrategyError as e:
        # 钩子缺失是警告，不是错误（允许保存草稿）
        warnings.append({
            "line": None,
            "severity": "warning",
            "code": "MISSING_HOOK",
            "message": str(e),
        })
    except StrategyLoadError as e:
        errors.append({
            "line": getattr(e, "line", None),
            "severity": "error",
            "code": "LOAD_ERROR",
            "message": str(e),
        })

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


__all__ = [
    "StrategyInstance",
    "StrategyLoader",
    "StrategyLoadError",
    "InvalidStrategyError",
    "StrategyTimeoutError",
    "validate_code",
    "REQUIRED_HOOKS",
    "OPTIONAL_HOOKS",
]
