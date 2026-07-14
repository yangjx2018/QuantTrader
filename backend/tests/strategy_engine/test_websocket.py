"""WebSocket 管理器单元测试（任务 3.5）。

验证：
- WebSocketManager 连接/断开行为
- 广播消息给订阅者
- 超时管理逻辑
- 全局单例
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from strategy_engine.runtime.websocket import (
    WebSocketManager,
    get_ws_manager,
    PING_INTERVAL,
    PONG_TIMEOUT,
)


class TestWebSocketManager:
    """WebSocket 连接管理测试"""

    def test_singleton(self):
        """get_ws_manager 返回同一实例。"""
        m1 = get_ws_manager()
        m2 = get_ws_manager()
        assert m1 is m2

    @pytest.mark.asyncio
    async def test_add_connection(self):
        """添加连接成功。"""
        manager = WebSocketManager()
        ws = AsyncMock()
        await manager.add(1, ws)
        # 连接存在
        assert 1 in manager._connections
        assert ws in manager._connections[1]

    @pytest.mark.asyncio
    async def test_add_multiple_connections(self):
        """同一策略多个连接。"""
        manager = WebSocketManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await manager.add(1, ws1)
        await manager.add(1, ws2)
        assert len(manager._connections[1]) == 2

    @pytest.mark.asyncio
    async def test_remove_connection(self):
        """移除连接成功。"""
        manager = WebSocketManager()
        ws = AsyncMock()
        await manager.add(1, ws)
        await manager.remove(1, ws)
        assert 1 not in manager._connections

    @pytest.mark.asyncio
    async def test_remove_cleans_empty_group(self):
        """移除最后一个连接后清理分组。"""
        manager = WebSocketManager()
        ws = AsyncMock()
        await manager.add(1, ws)
        await manager.remove(1, ws)
        assert 1 not in manager._connections

    @pytest.mark.asyncio
    async def test_broadcast_to_empty(self):
        """广播到无订阅者的策略返回 0。"""
        manager = WebSocketManager()
        sent = await manager.broadcast(999, {"type": "test"})
        assert sent == 0

    @pytest.mark.asyncio
    async def test_broadcast_sends_message(self):
        """广播成功发送消息给所有订阅者。"""
        manager = WebSocketManager()
        ws = AsyncMock()
        ws.send_text = AsyncMock()
        await manager.add(1, ws)

        sent = await manager.broadcast(1, {"type": "code_updated", "strategy_id": 1})
        assert sent == 1
        ws.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_cleans_dead_connections(self):
        """广播时清理已断开的连接。"""
        manager = WebSocketManager()
        ws_dead = AsyncMock()
        ws_dead.send_text = AsyncMock(side_effect=Exception("disconnected"))
        ws_alive = AsyncMock()
        ws_alive.send_text = AsyncMock()

        await manager.add(1, ws_dead)
        await manager.add(1, ws_alive)

        sent = await manager.broadcast(1, {"type": "test"})
        assert sent == 1  # 只有 alive 收到
        assert ws_dead not in manager._connections.get(1, set())
        assert ws_alive in manager._connections.get(1, set())


class TestWebSocketConnectionLifecycle:
    """WebSocket 连接生命周期测试"""

    @pytest.mark.asyncio
    async def test_handle_connection_cleanup_on_disconnect(self):
        """客户端断开时清理连接。"""
        manager = WebSocketManager()
        ws = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=Exception("disconnected"))

        await manager.handle_connection(ws, 1)
        assert 1 not in manager._connections

    @pytest.mark.asyncio
    async def test_handle_connection_ping_pong(self):
        """ping/pong 保持连接活跃。"""
        manager = WebSocketManager()

        # 先超时 → 触发 ping，然后返回 pong
        call_count = [0]

        async def receive_text():
            call_count[0] += 1
            if call_count[0] == 1:
                # 第一次调用：超时（返回前会先触发 ping 发送）
                import asyncio
                raise asyncio.TimeoutError()
            else:
                return "pong"

        ws = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=receive_text)
        ws.send_text = AsyncMock()

        # 使用 patch 控制 asyncio.wait_for 的行为
        with patch("asyncio.wait_for") as mock_wait_for:
            # 第一次：超时
            # 第二次：返回 "pong"
            # 第三次：抛出异常以退出循环
            mock_wait_for.side_effect = [
                asyncio.TimeoutError(),  # 30s 超时 → 触发 ping
                "pong",                   # 收到 pong
                Exception("end test"),    # 退出循环
            ]

            await manager.handle_connection(ws, 1)

        # 连接应该已清理
        assert 1 not in manager._connections

    @pytest.mark.asyncio
    async def test_handle_connection_pong_timeout_disconnects(self):
        """pong 超时断开连接。"""
        manager = WebSocketManager()
        ws = AsyncMock()
        ws.send_text = AsyncMock()

        with patch("asyncio.wait_for") as mock_wait_for:
            # 第一次超时 → 发 ping
            # 第二次超时 → pong timeout → 断开
            mock_wait_for.side_effect = [
                asyncio.TimeoutError(),
                asyncio.TimeoutError(),
            ]

            await manager.handle_connection(ws, 1)

        assert 1 not in manager._connections


# asyncio 导入
import asyncio
