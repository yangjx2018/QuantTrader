"""同花顺桌面自动化运行时开关（从环境变量读取，避免改动公共 config）。"""

from __future__ import annotations

import os


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", ""}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def skip_context_status_seconds() -> int:
    """最近一次成功 status 可复用秒数；0 表示禁用缓存。"""
    return max(0, env_int("THS_SKIP_CONTEXT_STATUS_SECONDS", 180))


def grid_cache_seconds() -> int:
    """表格剪贴板读取缓存秒数；0 表示禁用。"""
    return max(0, env_int("THS_GRID_CACHE_SECONDS", 8))


def light_verify() -> bool:
    """下单后轻量确认：只查当日委托，跳过成交/资金全量回查。"""
    return env_bool("THS_LIGHT_VERIFY", True)


def skip_post_order_confirm() -> bool:
    """跳过 router 层二次 confirm_order（会再连一次同花顺）。"""
    return env_bool("THS_SKIP_POST_ORDER_CONFIRM", True)


def status_probe_timeout_seconds() -> int:
    """仅探测状态时等待主窗口的秒数（不自动启动客户端）。"""
    return max(1, env_int("THS_STATUS_PROBE_TIMEOUT_SECONDS", 5))


def dialog_poll_seconds() -> float:
    """弹窗探测间隔；轻量模式下更短。"""
    if light_verify():
        return 0.15
    return 0.5
