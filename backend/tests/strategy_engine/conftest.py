"""strategy_engine 测试配置。

提供 test_db fixture 用于仓储层测试。
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from common.database import get_db, async_session


@pytest.fixture
async def test_db():
    """提供测试用的数据库会话。

    每个测试使用独立的数据库会话，测试结束后自动回滚。
    """
    async with async_session() as session:
        yield session
        await session.rollback()
