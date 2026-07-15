"""pytest 全局配置。

依赖 pytest.ini 的 asyncio_default_test_loop_scope=session 让所有 async 测试
共享同一个 event loop，避免模块级 engine 跨 loop 出现的
'attached to a different loop' / 'Event loop is closed' 问题。
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
async def _dispose_engine_after_session():
    """session 结束后 dispose common.database.engine 的连接池。"""
    yield
    from common.database import engine
    try:
        await engine.dispose()
    except Exception:
        pass
