"""策略沙箱：受限 globals + import/builtin 白名单。

设计原则：
- 策略代码通过 exec() 在沙箱内执行
- 删除危险 builtins（open / eval / exec / __import__ / compile / globals 等）
- 替换 __import__ 为白名单版（仅允许 math/statistics/datetime/decimal/json/collections）
- 显式 __builtins__ = dict（不是 module），阻止通过 __builtins__ 取回真实 builtins
- 不假设沙箱能 100% 防逃逸，生产部署应叠加容器隔离
"""

from __future__ import annotations

import builtins
import importlib
from types import ModuleType
from typing import Any

# 用户代码允许 import 的模块白名单（按 specs/strategy-dsl-runtime Requirement: 沙箱白名单 import）
ALLOWED_IMPORTS: frozenset[str] = frozenset({
    "math",
    "statistics",
    "datetime",
    "decimal",
    "json",
    "collections",
})

# 用户代码禁止访问的 builtins（按 specs/strategy-dsl-runtime Requirement: 沙箱禁用危险 builtins）
FORBIDDEN_BUILTINS: frozenset[str] = frozenset({
    "open",
    "eval",
    "exec",
    "compile",
    "__import__",
    "globals",
    "locals",
    "vars",
    "input",
    "breakpoint",
    "exit",
    "quit",
    "memoryview",
    "getattr",  # 防止通过 getattr(builtins, 'open') 逃逸
    "setattr",
    "delattr",
})


class ForbiddenImportError(ImportError):
    """用户尝试 import 白名单外的模块时抛出。"""


def _make_restricted_import():
    """构造受限的 __import__ 函数。

    仅允许 ALLOWED_IMPORTS 中的顶层模块；子模块同样受顶层白名单约束。
    """

    real_import = builtins.__import__

    def restricted_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
        top = name.split(".")[0]
        if top not in ALLOWED_IMPORTS:
            raise ForbiddenImportError(
                f"forbidden import: {name} (allowed: {sorted(ALLOWED_IMPORTS)})"
            )
        return real_import(name, globals, locals, fromlist, level)

    return restricted_import


def _build_safe_builtins() -> dict[str, Any]:
    """从真实 builtins 拷贝白名单内函数，返回 dict（不是 module）。"""
    safe: dict[str, Any] = {}
    for name in dir(builtins):
        if name.startswith("__") and name != "__build_class__":
            # 移除 __dunder__（除 __build_class__ 是 class 定义必需）
            continue
        if name in FORBIDDEN_BUILTINS:
            continue
        safe[name] = getattr(builtins, name)
    # 关键替换：__import__ 用受限版
    safe["__import__"] = _make_restricted_import()
    return safe


def build_safe_globals(extra_injects: dict[str, Any]) -> dict[str, Any]:
    """构造策略代码执行所需的受限 globals。

    Args:
        extra_injects: 引擎额外注入的对象，例如 g / log / get_history / order_*

    Returns:
        globals dict，可直接传给 exec(code, globals)。

    安全保证：
        - __builtins__ 是 dict 而非 module
        - 不含 open/eval/exec/__import__ 等危险项
        - __import__ 是白名单版
    """
    safe_globals: dict[str, Any] = {"__builtins__": _build_safe_builtins()}
    safe_globals.update(extra_injects)
    return safe_globals


def is_safe_module(name: str) -> bool:
    """工具函数：判断模块名是否在白名单内。"""
    return name.split(".")[0] in ALLOWED_IMPORTS


__all__ = [
    "ALLOWED_IMPORTS",
    "FORBIDDEN_BUILTINS",
    "ForbiddenImportError",
    "build_safe_globals",
    "is_safe_module",
]
