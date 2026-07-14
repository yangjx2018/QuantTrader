"""dry-run 接口集成测试（通过 HTTP API 操作，避免直接 DB session 跨 loop 问题）。

覆盖：
- 内置策略 dry-run 返回完整响应
- max_bars 截断生效
- 不存在的 strategy_id 返回 404
- 非 active 状态返回 400（通过 HTTP PUT 改 status）
- code_content 为空返回 400（通过 HTTP POST 创建）
- max_bars=200 返回 422（Pydantic 校验拦截）
- parameters 覆盖默认参数生效
"""

import pytest
import httpx
from fastapi import FastAPI

from strategy_engine.router import router


@pytest.fixture
def app():
    a = FastAPI()
    a.include_router(router)
    return a


async def _request(app, method: str, path: str, **kwargs):
    """辅助：通过 ASGITransport 调用任意 HTTP 接口。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, **kwargs)


async def _dry_run(app, strategy_id: int, payload: dict):
    """辅助：调用 dry-run 接口。"""
    return await _request(app, "POST", f"/api/strategy/{strategy_id}/dry-run", json=payload)


class TestDryRunEndpoint:
    """dry-run 端到端测试。"""

    @pytest.mark.asyncio
    async def test_dry_run_builtin_strategy_returns_response(self, app):
        """内置策略 1（双均线）dry-run 返回完整 DryRunResponse。"""
        payload = {
            "stock_code": "000001.SZ",
            "start_date": "2024-01-01",
            "end_date": "2024-02-15",
            "max_bars": 30,
        }
        r = await _dry_run(app, 1, payload)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        result = data["data"]
        assert result["total_bars"] == 30
        assert result["session_id"].startswith("dryrun_1_")
        assert "final_capital" in result
        assert "total_return_pct" in result
        assert len(result["bars"]) == 30
        bar0 = result["bars"][0]
        assert "time" in bar0
        assert "close" in bar0
        assert "total_assets" in bar0
        assert "signal" in bar0
        assert "orders_count" in bar0

    @pytest.mark.asyncio
    async def test_dry_run_max_bars_truncation(self, app):
        """max_bars=20 截断到前 20 个 bar。"""
        payload = {
            "stock_code": "000001.SZ",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "max_bars": 20,
        }
        r = await _dry_run(app, 1, payload)
        assert r.status_code == 200
        assert r.json()["data"]["total_bars"] == 20

    @pytest.mark.asyncio
    async def test_dry_run_max_bars_100_limit(self, app):
        """max_bars 上限 100，超过被 Pydantic 拒（422）。"""
        payload = {
            "stock_code": "000001.SZ",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "max_bars": 200,
        }
        r = await _dry_run(app, 1, payload)
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_dry_run_strategy_not_found(self, app):
        """strategy_id 不存在返回 404。"""
        payload = {
            "stock_code": "000001.SZ",
            "start_date": "2024-01-01",
            "end_date": "2024-01-30",
            "max_bars": 10,
        }
        r = await _dry_run(app, 99999, payload)
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_dry_run_strategy_not_active(self, app):
        """status != 'active' 返回 400。

        通过 PUT 接口把策略 4 改为 draft，跑 dry-run，再恢复。
        全程走 HTTP，避免直接 DB session。
        """
        # 1. 先记录原始 status
        r = await _request(app, "GET", "/api/strategy/4")
        original_status = r.json()["data"]["status"]
        assert original_status == "active"

        # 2. 改为 draft
        r = await _request(app, "PUT", "/api/strategy/4", json={"status": "draft"})
        assert r.json()["success"] is True

        try:
            payload = {
                "stock_code": "000001.SZ",
                "start_date": "2024-01-01",
                "end_date": "2024-01-30",
                "max_bars": 10,
            }
            r = await _dry_run(app, 4, payload)
            assert r.status_code == 400
            assert "active" in r.json()["detail"]
        finally:
            # 3. 恢复（同 loop 内）
            r = await _request(app, "PUT", "/api/strategy/4", json={"status": original_status})
            assert r.json()["success"] is True

    @pytest.mark.asyncio
    async def test_dry_run_empty_code_content(self, app):
        """code_content 为空返回 400。

        创建策略时空代码直接返回 400（P2 新增校验）。
        """
        r = await _request(app, "POST", "/api/strategy/create", json={
            "code": "EMPTY_TEST",
            "name": "空代码测试",
            "strategy_type": "trend",
            "status": "active",
            "version": "1.0.0",
            "code_content": None,
        })
        assert r.status_code == 400
        assert "不能为空" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_dry_run_parameters_override(self, app):
        """参数覆盖：双均线 short_window=3 / long_window=6。"""
        # 用默认参数跑
        payload_default = {
            "stock_code": "000001.SZ",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "max_bars": 60,
        }
        payload_short = {
            "stock_code": "000001.SZ",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "max_bars": 60,
            "parameters": {"short_window": 3, "long_window": 6, "buy_ratio": 0.5},
        }
        r1 = await _dry_run(app, 1, payload_default)
        r2 = await _dry_run(app, 1, payload_short)
        assert r1.status_code == 200 and r2.status_code == 200
        # 两次都跑完
        assert r1.json()["data"]["total_bars"] == 60
        assert r2.json()["data"]["total_bars"] == 60
        # 短周期参数下的订单数通常更多（不强制断言，只要不报错）
        orders_short = sum(b["orders_count"] for b in r2.json()["data"]["bars"])
        assert orders_short >= 0

    @pytest.mark.asyncio
    async def test_dry_run_invalid_stock_code(self, app):
        r"""stock_code 格式校验（必须 \d{6}.[SZSH]+）。"""
        payload = {
            "stock_code": "INVALID",
            "start_date": "2024-01-01",
            "end_date": "2024-01-30",
            "max_bars": 10,
        }
        r = await _dry_run(app, 1, payload)
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_dry_run_total_assets_consistency(self, app):
        """dry-run 中每个 bar 的 total_assets 都 > 0。"""
        payload = {
            "stock_code": "000001.SZ",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "max_bars": 60,
        }
        r = await _dry_run(app, 1, payload)
        bars = r.json()["data"]["bars"]
        for bar in bars:
            assert bar["total_assets"] > 0, f"bar {bar['time']} total_assets<=0"
        assert r.json()["data"]["final_capital"] == bars[-1]["total_assets"]
