"""test_live_execution.py: 实盘对接集成测试。

覆盖：
- 5.1: load_strategy 函数（缓存命中 / 未命中 / 策略不存在 / 未启用）
- 5.2: /reload 路由（200 响应）
- 5.3: /reload 异常处理（404 / 400）
"""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport

from strategy_engine.router import router
from common.database import get_db


@pytest.fixture
async def test_client():
    """提供测试用的 AsyncClient。"""
    app = FastAPI()
    app.include_router(router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_load_strategy_cache_miss(test_client, test_db):
    """load_strategy: 缓存未命中时从 DB 加载。"""
    from strategy_engine.service import load_strategy
    from strategy_engine.runtime.registry import get_global_registry

    # 清空缓存
    registry = get_global_registry()
    await registry.clear()

    # 加载策略 1（内置策略，应该存在）
    instance = await load_strategy(test_db, 1)

    assert instance is not None
    # StrategyInstance 有 globals_dict 和 parameters 属性
    assert hasattr(instance, 'globals_dict')
    assert hasattr(instance, 'parameters')
    # 应该包含 initialize 和 handle_data 钩子
    assert instance.has_hook('initialize')
    assert instance.has_hook('handle_data')


@pytest.mark.asyncio
async def test_load_strategy_cache_hit(test_client, test_db):
    """load_strategy: 缓存命中时直接返回。"""
    from strategy_engine.service import load_strategy
    from strategy_engine.runtime.registry import get_global_registry

    # 清空缓存
    registry = get_global_registry()
    await registry.clear()

    # 第一次加载（缓存未命中）
    instance1 = await load_strategy(test_db, 1)

    # 第二次加载（应该缓存命中）
    instance2 = await load_strategy(test_db, 1)

    # 应该返回同一个实例（对象 ID 相同）
    assert id(instance1) == id(instance2)


@pytest.mark.asyncio
async def test_load_strategy_not_found(test_client, test_db):
    """load_strategy: 策略不存在时抛出 StrategyNotFound。"""
    from strategy_engine.service import load_strategy
    from strategy_engine.exceptions import StrategyNotFound

    with pytest.raises(StrategyNotFound):
        await load_strategy(test_db, 99999)


@pytest.mark.asyncio
async def test_load_strategy_not_active(test_client, test_db):
    """load_strategy: 策略未启用时抛出 StrategyNotActive。"""
    from strategy_engine.service import load_strategy
    from strategy_engine.exceptions import StrategyNotActive
    from strategy_engine.repository import StrategyRepository

    # 创建一个 draft 状态的策略
    repo = StrategyRepository(test_db)
    strategy = await repo.create({
        "code": "TEST_DRAFT",
        "name": "测试草稿策略",
        "strategy_type": "trend",
        "status": "draft",
        "code_content": "def initialize(context): pass\ndef handle_data(context, data): pass",
    })

    try:
        with pytest.raises(StrategyNotActive):
            await load_strategy(test_db, strategy.id)
    finally:
        # 清理
        await repo.delete(strategy.id)


@pytest.mark.asyncio
async def test_reload_route_success(test_client, test_db):
    """POST /api/strategy/{id}/reload: 成功重新加载策略。"""
    # 先加载策略 1（确保它在缓存中）
    from strategy_engine.service import load_strategy
    await load_strategy(test_db, 1)

    # 调用 /reload 路由
    response = await test_client.post("/api/strategy/1/reload")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["strategy_id"] == 1
    assert data["data"]["status"] == "reloaded"


@pytest.mark.asyncio
async def test_reload_route_not_found(test_client):
    """POST /api/strategy/{id}/reload: 策略不存在返回 404。"""
    response = await test_client.post("/api/strategy/99999/reload")

    assert response.status_code == 404
    assert "不存在" in response.json()["detail"]


@pytest.mark.asyncio
async def test_reload_route_invalid_code(test_client, test_db):
    """POST /api/strategy/{id}/reload: 策略代码无效返回 400。"""
    from strategy_engine.repository import StrategyRepository

    # 创建一个代码无效的策略
    repo = StrategyRepository(test_db)
    strategy = await repo.create({
        "code": "TEST_INVALID",
        "name": "测试无效代码",
        "strategy_type": "trend",
        "status": "active",
        "code_content": "def invalid_syntax(",  # 语法错误
    })

    # 提交事务以确保策略被保存到数据库
    await test_db.commit()

    try:
        response = await test_client.post(f"/api/strategy/{strategy.id}/reload")
        assert response.status_code == 400
        assert "代码" in response.json()["detail"] or "语法" in response.json()["detail"]
    finally:
        # 清理
        await repo.delete(strategy.id)
        await test_db.commit()


@pytest.mark.asyncio
async def test_end_to_end_load_and_reload(test_client, test_db):
    """端到端测试：load_strategy + /reload 完整流程。"""
    from strategy_engine.service import load_strategy
    from strategy_engine.runtime.registry import get_global_registry

    # 清空缓存
    registry = get_global_registry()
    await registry.clear()

    # 1. 加载策略（缓存未命中）
    instance1 = await load_strategy(test_db, 1)
    assert instance1 is not None

    # 2. 再次加载（缓存命中）
    instance2 = await load_strategy(test_db, 1)
    assert instance1 is instance2

    # 3. 调用 /reload 清除缓存并重新加载
    response = await test_client.post("/api/strategy/1/reload")
    assert response.status_code == 200

    # 4. 再次加载（应该得到新实例）
    instance3 = await load_strategy(test_db, 1)
    assert instance3 is not instance1  # 应该是新实例
