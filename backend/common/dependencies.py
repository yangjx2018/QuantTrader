from typing import Annotated
from fastapi import Depends
from .database import get_db
from .config import get_settings, Settings

# Lazy import to avoid circular dependency
_data_source = None


def get_data_source():
    """获取数据源适配器（全局单例，延迟加载）。

    由 .env MARKET_DATA_SOURCE 控制：
    - akshare：真实 A 股行情（失败可降级到 mock）
    - mock：纯模拟数据
    """
    global _data_source
    if _data_source is None:
        settings = get_settings()
        source = (settings.MARKET_DATA_SOURCE or "akshare").strip().lower()
        if source == "mock":
            from api_data.adapters.mock import MockAdapter

            _data_source = MockAdapter()
        else:
            try:
                from api_data.adapters.akshare import AkshareAdapter

                _data_source = AkshareAdapter()
            except Exception:
                from api_data.adapters.mock import MockAdapter

                _data_source = MockAdapter()
    return _data_source


def reset_data_source() -> None:
    """测试/热切换用：清空单例。"""
    global _data_source
    _data_source = None


__all__ = ["get_db", "get_settings", "Settings", "get_data_source", "reset_data_source"]
