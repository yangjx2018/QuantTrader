"""WebSocket 连接管理器：策略热更新推送。

设计要点：
- 按 strategy_id 分组管理连接
- 支持广播消息给所有订阅者
- 30 秒无消息发送 ping，10 秒无 pong 断开
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# 连接超时配置
PING_INTERVAL = 30  # 秒
PONG_TIMEOUT = 10   # 秒


class WebSocketManager:
    """WebSocket 连接管理器（单例）。

    用法：
        manager = get_ws_manager()
        await manager.add(strategy_id, websocket)
        await manager.broadcast(strategy_id, {"type": "code_updated", ...})
        await manager.remove(strategy_id, websocket)
    """

    def __init__(self):
        # {strategy_id: set[WebSocket]}
        self._connections: dict[int, set[WebSocket]] = {}

    async def add(self, strategy_id: int, websocket: WebSocket) -> None:
        """添加连接。"""
        if strategy_id not in self._connections:
            self._connections[strategy_id] = set()
        self._connections[strategy_id].add(websocket)
        logger.info(
            "WebSocket connected: strategy_id=%d, total_connections=%d",
            strategy_id, len(self._connections[strategy_id]),
        )

    async def remove(self, strategy_id: int, websocket: WebSocket) -> None:
        """移除连接。"""
        if strategy_id in self._connections:
            self._connections[strategy_id].discard(websocket)
            if not self._connections[strategy_id]:
                del self._connections[strategy_id]
        logger.info(
            "WebSocket disconnected: strategy_id=%d", strategy_id,
        )

    async def broadcast(self, strategy_id: int, message: dict) -> int:
        """向指定策略的所有订阅者广播消息。

        Returns:
            成功发送的数量
        """
        if strategy_id not in self._connections:
            return 0

        payload = json.dumps(message, ensure_ascii=False)
        dead: list[WebSocket] = []
        sent = 0

        for ws in list(self._connections[strategy_id]):
            try:
                await ws.send_text(payload)
                sent += 1
            except Exception:
                dead.append(ws)

        # 清理已断开的连接
        for ws in dead:
            await self.remove(strategy_id, ws)

        return sent

    async def handle_connection(
        self, websocket: WebSocket, strategy_id: int
    ) -> None:
        """管理单个 WebSocket 连接的生命周期（ping/pong + 断开清理）。

        用法：
            await manager.handle_connection(websocket, strategy_id)
        """
        await self.add(strategy_id, websocket)
        try:
            while True:
                try:
                    data = await asyncio.wait_for(
                        websocket.receive_text(), timeout=PING_INTERVAL
                    )
                    # 收到任何消息都视为活跃（包括 pong）
                    if data == "pong":
                        logger.debug(
                            "pong from strategy_id=%d", strategy_id
                        )
                except asyncio.TimeoutError:
                    # 30 秒无消息，发送 ping
                    try:
                        await websocket.send_text("ping")
                        # 等 pong，最多 10 秒
                        pong = await asyncio.wait_for(
                            websocket.receive_text(), timeout=PONG_TIMEOUT
                        )
                        if pong != "pong":
                            logger.warning(
                                "Expected pong, got: %s from strategy_id=%d",
                                pong, strategy_id,
                            )
                            break
                    except asyncio.TimeoutError:
                        logger.warning(
                            "WebSocket pong timeout: strategy_id=%d", strategy_id
                        )
                        break
                    except Exception:
                        break
        except Exception:
            pass
        finally:
            await self.remove(strategy_id, websocket)


# 全局单例
_ws_manager: WebSocketManager | None = None


def get_ws_manager() -> WebSocketManager:
    """获取全局 WebSocketManager 单例。"""
    global _ws_manager
    if _ws_manager is None:
        _ws_manager = WebSocketManager()
    return _ws_manager


__all__ = ["WebSocketManager", "get_ws_manager"]
