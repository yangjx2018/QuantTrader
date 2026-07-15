"""StrategyRegistry: LRU 缓存管理策略实例。

设计要点：
- 最大缓存 100 个策略实例（LRU 淘汰）
- 使用 asyncio.Lock 保护并发访问
- 支持热加载（通过 evict 强制刷新）
- 线程安全（asyncio.Lock）
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from strategy_engine.runtime.loader import StrategyInstance


# 最大缓存实例数
MAX_CACHE_SIZE = 100


class StrategyRegistry:
    """策略实例 LRU 缓存管理器。

    用法：
        registry = StrategyRegistry()
        instance = await registry.get(strategy_id, loader_func)
        registry.evict(strategy_id)
    """

    def __init__(self, max_size: int = MAX_CACHE_SIZE):
        """初始化注册表。

        Args:
            max_size: 最大缓存实例数（默认 100）
        """
        self._cache: OrderedDict[int, StrategyInstance] = OrderedDict()
        self._max_size = max_size
        self._lock = asyncio.Lock()

    async def get(
        self,
        strategy_id: int,
        loader_func=None,
    ) -> Optional[StrategyInstance]:
        """获取策略实例（缓存命中则返回，否则调用 loader_func 加载）。

        Args:
            strategy_id: 策略 ID
            loader_func: 异步加载函数 async def load(strategy_id) -> StrategyInstance
                         仅在缓存未命中时调用

        Returns:
            StrategyInstance 或 None（如果 loader_func 返回 None）

        Raises:
            RuntimeError: 如果 loader_func 为 None 且缓存未命中
        """
        async with self._lock:
            if strategy_id in self._cache:
                # 缓存命中：移动到末尾（最近使用）
                self._cache.move_to_end(strategy_id)
                return self._cache[strategy_id]

            # 缓存未命中：调用 loader_func 加载
            if loader_func is None:
                return None

            instance = await loader_func(strategy_id)
            if instance is None:
                return None

            # 添加到缓存
            self._cache[strategy_id] = instance

            # LRU 淘汰：超过 max_size 时淘汰最久未访问（最左边）
            if len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

            return instance

    async def put(self, strategy_id: int, instance: StrategyInstance) -> None:
        """强制缓存策略实例（覆盖已有缓存）。

        Args:
            strategy_id: 策略 ID
            instance: 策略实例
        """
        async with self._lock:
            self._cache[strategy_id] = instance

            # LRU 淘汰：超过 max_size 时淘汰最久未访问（最左边）
            if len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    async def evict(self, strategy_id: int) -> bool:
        """强制淘汰指定策略实例（用于热加载）。

        Args:
            strategy_id: 策略 ID

        Returns:
            True 如果成功淘汰，False 如果缓存中不存在
        """
        async with self._lock:
            if strategy_id in self._cache:
                del self._cache[strategy_id]
                return True
            return False

    async def clear(self) -> None:
        """清空所有缓存。"""
        async with self._lock:
            self._cache.clear()

    async def size(self) -> int:
        """返回当前缓存大小。"""
        async with self._lock:
            return len(self._cache)

    async def contains(self, strategy_id: int) -> bool:
        """检查策略是否在缓存中。"""
        async with self._lock:
            return strategy_id in self._cache


# 全局单例（可选）
_global_registry: Optional[StrategyRegistry] = None


def get_global_registry() -> StrategyRegistry:
    """获取全局 StrategyRegistry 单例。"""
    global _global_registry
    if _global_registry is None:
        _global_registry = StrategyRegistry()
    return _global_registry
