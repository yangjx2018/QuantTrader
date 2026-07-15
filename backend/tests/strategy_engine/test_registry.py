"""test_registry.py: StrategyRegistry 单元测试。

覆盖：
- 4.1: StrategyRegistry 接口定义（get / put / evict）
- 4.2: LRU 淘汰逻辑（max 100 实例）
- 4.3: 线程安全（asyncio.Lock）
"""

import asyncio
import pytest

from strategy_engine.runtime.registry import StrategyRegistry, MAX_CACHE_SIZE


class MockStrategyInstance:
    """模拟策略实例（用于测试）。"""

    def __init__(self, strategy_id: int, name: str = "test"):
        self.strategy_id = strategy_id
        self.name = name


@pytest.fixture
def registry():
    """提供测试用的 StrategyRegistry 实例。"""
    return StrategyRegistry(max_size=10)  # 使用较小的 max_size 便于测试


@pytest.mark.asyncio
async def test_get_cache_miss_returns_none_without_loader(registry):
    """get() 缓存未命中且无 loader_func 时返回 None。"""
    result = await registry.get(999)
    assert result is None


@pytest.mark.asyncio
async def test_get_cache_miss_with_loader_calls_loader(registry):
    """get() 缓存未命中时调用 loader_func 加载实例。"""
    loader_called = []

    async def mock_loader(strategy_id):
        loader_called.append(strategy_id)
        return MockStrategyInstance(strategy_id, f"strategy_{strategy_id}")

    result = await registry.get(1, mock_loader)
    assert result is not None
    assert result.strategy_id == 1
    assert result.name == "strategy_1"
    assert loader_called == [1]


@pytest.mark.asyncio
async def test_get_cache_hit_returns_cached_instance(registry):
    """get() 缓存命中时返回缓存实例（不调用 loader_func）。"""
    # 先 put 一个实例
    instance = MockStrategyInstance(1, "cached")
    await registry.put(1, instance)

    loader_called = []

    async def mock_loader(strategy_id):
        loader_called.append(strategy_id)
        return MockStrategyInstance(strategy_id, "new")

    # get 应该返回缓存的实例，不调用 loader
    result = await registry.get(1, mock_loader)
    assert result is instance
    assert result.name == "cached"
    assert loader_called == []  # loader 未被调用


@pytest.mark.asyncio
async def test_put_overwrites_existing_cache(registry):
    """put() 覆盖已有缓存。"""
    instance1 = MockStrategyInstance(1, "old")
    await registry.put(1, instance1)

    instance2 = MockStrategyInstance(1, "new")
    await registry.put(1, instance2)

    result = await registry.get(1)
    assert result is instance2
    assert result.name == "new"


@pytest.mark.asyncio
async def test_evict_removes_cached_instance(registry):
    """evict() 删除缓存实例。"""
    instance = MockStrategyInstance(1, "test")
    await registry.put(1, instance)

    assert await registry.contains(1)
    result = await registry.evict(1)
    assert result is True
    assert not await registry.contains(1)


@pytest.mark.asyncio
async def test_evict_returns_false_if_not_cached(registry):
    """evict() 缓存不存在时返回 False。"""
    result = await registry.evict(999)
    assert result is False


@pytest.mark.asyncio
async def test_lru_eviction_when_exceeds_max_size(registry):
    """LRU 淘汰：超过 max_size 时淘汰最久未访问。"""
    # registry.max_size = 10，put 11 个实例
    for i in range(11):
        instance = MockStrategyInstance(i, f"strategy_{i}")
        await registry.put(i, instance)

    # 第 0 个应该被淘汰（最久未访问）
    assert not await registry.contains(0)
    assert await registry.contains(10)  # 第 10 个应该在缓存中


@pytest.mark.asyncio
async def test_lru_eviction_respects_access_order(registry):
    """LRU 淘汰：最近访问的实例不会被淘汰。"""
    # put 10 个实例
    for i in range(10):
        instance = MockStrategyInstance(i, f"strategy_{i}")
        await registry.put(i, instance)

    # 访问第 0 个（移动到末尾）
    await registry.get(0)

    # put 第 11 个，应该淘汰第 1 个（而不是第 0 个）
    instance = MockStrategyInstance(10, "strategy_10")
    await registry.put(10, instance)

    assert await registry.contains(0)  # 第 0 个最近访问过，不应该被淘汰
    assert not await registry.contains(1)  # 第 1 个最久未访问，应该被淘汰


@pytest.mark.asyncio
async def test_clear_removes_all_cached_instances(registry):
    """clear() 清空所有缓存。"""
    for i in range(5):
        instance = MockStrategyInstance(i, f"strategy_{i}")
        await registry.put(i, instance)

    assert await registry.size() == 5
    await registry.clear()
    assert await registry.size() == 0


@pytest.mark.asyncio
async def test_size_returns_cache_size(registry):
    """size() 返回当前缓存大小。"""
    assert await registry.size() == 0

    for i in range(5):
        instance = MockStrategyInstance(i, f"strategy_{i}")
        await registry.put(i, instance)

    assert await registry.size() == 5


@pytest.mark.asyncio
async def test_contains_checks_cache_membership(registry):
    """contains() 检查策略是否在缓存中。"""
    assert not await registry.contains(1)

    instance = MockStrategyInstance(1, "test")
    await registry.put(1, instance)

    assert await registry.contains(1)


@pytest.mark.asyncio
async def test_concurrent_get_no_race_condition(registry):
    """并发 get() 无竞态条件（asyncio.Lock 保护）。"""
    loader_call_count = []

    async def mock_loader(strategy_id):
        # 模拟异步加载延迟
        await asyncio.sleep(0.01)
        loader_call_count.append(strategy_id)
        return MockStrategyInstance(strategy_id, f"strategy_{strategy_id}")

    # 并发调用 get() 10 次（同一个 strategy_id）
    tasks = [registry.get(1, mock_loader) for _ in range(10)]
    results = await asyncio.gather(*tasks)

    # 所有结果应该相同（缓存生效）
    assert all(r.strategy_id == 1 for r in results)
    # loader 只应该被调用一次（缓存生效）
    assert len(loader_call_count) == 1


@pytest.mark.asyncio
async def test_concurrent_put_no_race_condition(registry):
    """并发 put() 无竞态条件（asyncio.Lock 保护）。"""
    async def mock_put(i):
        instance = MockStrategyInstance(i, f"strategy_{i}")
        await registry.put(i, instance)

    # 并发 put() 10 个实例
    tasks = [mock_put(i) for i in range(10)]
    await asyncio.gather(*tasks)

    # 所有实例都应该在缓存中
    assert await registry.size() == 10
    for i in range(10):
        assert await registry.contains(i)


@pytest.mark.asyncio
async def test_default_max_cache_size():
    """默认 max_size = 100。"""
    registry = StrategyRegistry()
    assert registry._max_size == MAX_CACHE_SIZE


@pytest.mark.asyncio
async def test_custom_max_cache_size():
    """自定义 max_size。"""
    registry = StrategyRegistry(max_size=50)
    assert registry._max_size == 50
