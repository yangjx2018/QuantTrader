"""router.py 路由测试（通过 httpx.AsyncClient + ASGITransport 测试）。

覆盖：
- 3.1: CRUD 路由（list, create, get, update, delete）
- 3.2: 校验与试运行路由（validate, options）
"""

import pytest
from fastapi import FastAPI
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from strategy_engine.router import router
from common.database import get_db


@pytest.fixture
async def test_db():
    """提供测试用的数据库会话，测试结束后自动回滚。"""
    from common.database import async_session
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
def app(test_db: AsyncSession):
    """创建 FastAPI 应用并覆盖 get_db 依赖。"""
    app = FastAPI()
    app.include_router(router)

    async def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest.fixture
async def client(app):
    """提供异步测试客户端"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        yield client


class TestStrategyCRUD:
    """3.1: CRUD 路由测试"""

    @pytest.mark.asyncio
    async def test_list_strategies_default(self, client):
        """GET /api/strategy/list 默认返回所有 active 策略"""
        response = await client.get("/api/strategy/list")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)
        assert data["total"] >= 4  # 至少有 4 个内置策略

    @pytest.mark.asyncio
    async def test_list_strategies_with_status_filter(self, client):
        """GET /api/strategy/list?status=active 过滤 active 策略"""
        response = await client.get("/api/strategy/list?status=active")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]) >= 4
        for strategy in data["data"]:
            assert strategy["status"] == "active"

    @pytest.mark.asyncio
    async def test_list_strategies_with_type_filter(self, client):
        """GET /api/strategy/list?strategy_type=trend 过滤 trend 策略"""
        response = await client.get("/api/strategy/list?strategy_type=trend")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        for strategy in data["data"]:
            assert strategy["strategy_type"] == "trend"

    @pytest.mark.asyncio
    async def test_list_strategies_with_pagination(self, client):
        """GET /api/strategy/list?limit=2&offset=0 分页"""
        response = await client.get("/api/strategy/list?limit=2&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]) <= 2

    @pytest.mark.asyncio
    async def test_create_strategy_success(self, client):
        """POST /api/strategy/create 创建策略成功"""
        payload = {
            "code": "TEST_CREATE_001",
            "name": "测试创建策略",
            "strategy_type": "trend",
            "status": "draft",
            "version": "1.0.0",
            "code_content": "def initialize(context):\n    pass\n\ndef handle_data(context, data):\n    pass",
        }
        response = await client.post("/api/strategy/create", json=payload)
        if response.status_code != 200:
            print(f"Error response: {response.json()}")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["code"] == "TEST_CREATE_001"
        assert data["data"]["name"] == "测试创建策略"

        # 清理
        await client.delete(f"/api/strategy/{data['data']['id']}")

    @pytest.mark.asyncio
    async def test_create_strategy_duplicate_code(self, client):
        """POST /api/strategy/create 重复 code 返回 400"""
        payload = {
            "code": "TEST_DUP_001",
            "name": "测试重复策略",
            "strategy_type": "trend",
            "status": "draft",
            "version": "1.0.0",
            "code_content": "def initialize(context):\n    pass\n\ndef handle_data(context, data):\n    pass",
        }
        # 创建第一个
        response1 = await client.post("/api/strategy/create", json=payload)
        assert response1.status_code == 200

        # 尝试创建重复的
        response2 = await client.post("/api/strategy/create", json=payload)
        assert response2.status_code == 400
        assert "已存在" in response2.json()["detail"]

        # 清理
        await client.delete(f"/api/strategy/{response1.json()['data']['id']}")

    @pytest.mark.asyncio
    async def test_get_strategy_success(self, client):
        """GET /api/strategy/{id} 获取策略详情成功"""
        response = await client.get("/api/strategy/1")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["id"] == 1
        assert "code" in data["data"]
        assert "name" in data["data"]

    @pytest.mark.asyncio
    async def test_get_strategy_not_found(self, client):
        """GET /api/strategy/{id} 不存在返回 404"""
        response = await client.get("/api/strategy/99999")
        assert response.status_code == 404
        assert "不存在" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_strategy_success(self, client):
        """PUT /api/strategy/{id} 更新策略成功"""
        # 先创建
        payload = {
            "code": "TEST_UPDATE_001",
            "name": "测试更新策略",
            "strategy_type": "trend",
            "status": "draft",
            "version": "1.0.0",
            "code_content": "def initialize(context):\n    pass\n\ndef handle_data(context, data):\n    pass",
        }
        create_response = await client.post("/api/strategy/create", json=payload)
        assert create_response.status_code == 200
        strategy_id = create_response.json()["data"]["id"]

        # 更新
        update_payload = {
            "name": "更新后的策略",
            "description": "更新后的描述",
        }
        response = await client.put(f"/api/strategy/{strategy_id}", json=update_payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["name"] == "更新后的策略"
        assert data["data"]["description"] == "更新后的描述"

        # 清理
        await client.delete(f"/api/strategy/{strategy_id}")

    @pytest.mark.asyncio
    async def test_update_strategy_not_found(self, client):
        """PUT /api/strategy/{id} 不存在返回 404"""
        update_payload = {"name": "test"}
        response = await client.put("/api/strategy/99999", json=update_payload)
        assert response.status_code == 404
        assert "不存在" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_strategy_success(self, client):
        """DELETE /api/strategy/{id} 删除策略成功"""
        # 先创建
        payload = {
            "code": "TEST_DELETE_001",
            "name": "测试删除策略",
            "strategy_type": "trend",
            "status": "draft",
            "version": "1.0.0",
            "code_content": "def initialize(context):\n    pass\n\ndef handle_data(context, data):\n    pass",
        }
        create_response = await client.post("/api/strategy/create", json=payload)
        assert create_response.status_code == 200
        strategy_id = create_response.json()["data"]["id"]

        # 删除
        response = await client.delete(f"/api/strategy/{strategy_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "策略删除成功"

        # 验证已删除
        get_response = await client.get(f"/api/strategy/{strategy_id}")
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_strategy_not_found(self, client):
        """DELETE /api/strategy/{id} 不存在返回 404"""
        response = await client.delete("/api/strategy/99999")
        assert response.status_code == 404
        assert "不存在" in response.json()["detail"]


class TestStrategyValidation:
    """3.2: 校验路由测试"""

    @pytest.mark.asyncio
    async def test_validate_code_success(self, client):
        """POST /api/strategy/validate 校验合法代码返回 valid=true"""
        payload = {
            "code_content": "def initialize(context):\n    pass\n\ndef handle_data(context, data):\n    pass",
        }
        response = await client.post("/api/strategy/validate", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["valid"] is True
        assert len(data["data"]["errors"]) == 0

    @pytest.mark.asyncio
    async def test_validate_code_syntax_error(self, client):
        """POST /api/strategy/validate 语法错误返回 valid=false + errors"""
        payload = {
            "code_content": "def initialize(context):\n    pass\n\ndef handle_data(context, data)\n    pass",
        }
        response = await client.post("/api/strategy/validate", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["valid"] is False
        assert len(data["data"]["errors"]) > 0
        assert data["data"]["errors"][0]["severity"] == "error"

    @pytest.mark.asyncio
    async def test_validate_code_sandbox_error(self, client):
        """POST /api/strategy/validate 沙箱违规返回 valid=false + errors"""
        payload = {
            "code_content": "import os\n\ndef initialize(context):\n    pass\n\ndef handle_data(context, data):\n    pass",
        }
        response = await client.post("/api/strategy/validate", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["valid"] is False
        assert len(data["data"]["errors"]) > 0

    @pytest.mark.asyncio
    async def test_validate_code_missing_hooks(self, client):
        """POST /api/strategy/validate 缺少钩子返回 valid=true + warnings"""
        payload = {
            "code_content": "def initialize(context):\n    pass",
        }
        response = await client.post("/api/strategy/validate", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["valid"] is True
        assert len(data["data"]["warnings"]) > 0


class TestStrategyOptions:
    """3.2: options 路由测试"""

    @pytest.mark.asyncio
    async def test_options_all_default(self, client):
        """GET /api/strategy/options/all 默认返回所有 active 策略"""
        response = await client.get("/api/strategy/options/all")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]) >= 4
        for option in data["data"]:
            assert "id" in option
            assert "name" in option
            assert "strategy_type" in option

    @pytest.mark.asyncio
    async def test_options_all_with_status_all(self, client):
        """GET /api/strategy/options/all?status=all 返回所有策略"""
        response = await client.get("/api/strategy/options/all?status=all")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]) >= 4

    @pytest.mark.asyncio
    async def test_options_all_with_status_active(self, client):
        """GET /api/strategy/options/all?status=active 只返回 active 策略"""
        response = await client.get("/api/strategy/options/all?status=active")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestStrategyVersions:
    """3.3: 版本管理路由测试"""

    @pytest.mark.asyncio
    async def test_get_versions_success(self, client):
        """GET /api/strategy/{id}/versions 获取版本历史成功"""
        response = await client.get("/api/strategy/1/versions")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)

    @pytest.mark.asyncio
    async def test_get_versions_not_found(self, client):
        """GET /api/strategy/{id}/versions 策略不存在返回 404"""
        response = await client.get("/api/strategy/99999/versions")
        assert response.status_code == 404
        assert "不存在" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_version_success(self, client):
        """POST /api/strategy/{id}/versions 创建版本成功"""
        # 先创建策略
        payload = {
            "code": "TEST_VERSION_001",
            "name": "测试版本策略",
            "strategy_type": "trend",
            "status": "draft",
            "version": "1.0.0",
            "code_content": "def initialize(context):\n    pass\n\ndef handle_data(context, data):\n    pass",
        }
        create_response = await client.post("/api/strategy/create", json=payload)
        assert create_response.status_code == 200
        strategy_id = create_response.json()["data"]["id"]

        # 创建版本（strategy_id 在 URL 路径中，不在 request body 中）
        version_payload = {
            "version": "1.0.1",
            "code_content": "def initialize(context):\n    pass\n\ndef handle_data(context, data):\n    pass",
            "change_log": "第一个版本",
        }
        response = await client.post(f"/api/strategy/{strategy_id}/versions", json=version_payload)
        if response.status_code != 200:
            print(f"Error response: {response.json()}")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["version"] == "1.0.1"

        # 清理
        await client.delete(f"/api/strategy/{strategy_id}")

    @pytest.mark.asyncio
    async def test_create_version_not_found(self, client):
        """POST /api/strategy/{id}/versions 策略不存在返回 404"""
        version_payload = {
            "version": "1.0.1",
            "code_content": "def initialize(context):\n    pass\n\ndef handle_data(context, data):\n    pass",
            "change_log": "test",
        }
        response = await client.post("/api/strategy/99999/versions", json=version_payload)
        assert response.status_code == 404
        assert "不存在" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_version_duplicate(self, client):
        """POST /api/strategy/{id}/versions 重复版本号返回 400"""
        # 先创建策略
        payload = {
            "code": "TEST_VERSION_DUP",
            "name": "测试版本重复",
            "strategy_type": "trend",
            "status": "draft",
            "version": "1.0.0",
            "code_content": "def initialize(context):\n    pass\n\ndef handle_data(context, data):\n    pass",
        }
        create_response = await client.post("/api/strategy/create", json=payload)
        assert create_response.status_code == 200
        strategy_id = create_response.json()["data"]["id"]

        # 创建第一个版本
        version_payload = {
            "version": "1.0.1",
            "code_content": "def initialize(context):\n    pass\n\ndef handle_data(context, data):\n    pass",
            "change_log": "first",
        }
        response1 = await client.post(f"/api/strategy/{strategy_id}/versions", json=version_payload)
        assert response1.status_code == 200

        # 尝试创建重复版本
        response2 = await client.post(f"/api/strategy/{strategy_id}/versions", json=version_payload)
        assert response2.status_code == 400
        assert "失败" in response2.json()["detail"]

        # 清理
        await client.delete(f"/api/strategy/{strategy_id}")


class TestEmptyCodeContent:
    """2.2: 策略代码为空场景测试"""

    @pytest.mark.asyncio
    async def test_create_strategy_empty_code_content(self, client):
        """POST /api/strategy/create 且 code_content 为空返回 400"""
        payload = {
            "code": "TEST_EMPTY_CODE",
            "name": "空代码策略",
            "strategy_type": "trend",
            "status": "draft",
            "version": "1.0.0",
            "code_content": "",
        }
        response = await client.post("/api/strategy/create", json=payload)
        assert response.status_code == 400
        assert "不能为空" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_strategy_none_code_content(self, client):
        """POST /api/strategy/create 且不传 code_content"""
        payload = {
            "code": "TEST_NULL_CODE",
            "name": "空代码策略2",
            "strategy_type": "trend",
            "status": "draft",
            "version": "1.0.0",
        }
        response = await client.post("/api/strategy/create", json=payload)
        assert response.status_code == 400
        assert "不能为空" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_strategy_whitespace_code_content(self, client):
        """POST /api/strategy/create 且 code_content 仅有空白返回 400"""
        payload = {
            "code": "TEST_SPACE_CODE",
            "name": "空白代码策略",
            "strategy_type": "trend",
            "status": "draft",
            "version": "1.0.0",
            "code_content": "   \n\t  ",
        }
        response = await client.post("/api/strategy/create", json=payload)
        assert response.status_code == 400
        assert "不能为空" in response.json()["detail"]


class TestDryRunEdgeCases:
    """4.4 & 2.5: dry-run 边界场景测试"""

    @pytest.mark.asyncio
    async def test_dry_run_strategy_not_active(self, client):
        """POST /api/strategy/{id}/dry-run 且 status='draft' 返回 400"""
        payload = {
            "code": "TEST_DRYRUN_DRAFT",
            "name": "试运行草稿策略",
            "strategy_type": "trend",
            "status": "draft",
            "version": "1.0.0",
            "code_content": "def initialize(context):\n    pass\n\ndef handle_data(context, data):\n    pass",
        }
        create_response = await client.post("/api/strategy/create", json=payload)
        assert create_response.status_code == 200
        strategy_id = create_response.json()["data"]["id"]

        dry_run_payload = {
            "stock_code": "000001.SZ",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "max_bars": 30,
        }
        response = await client.post(
            f"/api/strategy/{strategy_id}/dry-run", json=dry_run_payload
        )
        assert response.status_code == 400
        assert "非 active" in response.json()["detail"]

        await client.delete(f"/api/strategy/{strategy_id}")

    @pytest.mark.asyncio
    async def test_dry_run_max_bars_exceeds_limit(self, client):
        """POST /api/strategy/{id}/dry-run 且 max_bars=200 返回 422"""
        dry_run_payload = {
            "stock_code": "000001.SZ",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "max_bars": 200,
        }
        response = await client.post(
            "/api/strategy/1/dry-run", json=dry_run_payload
        )
        assert response.status_code == 422


class TestReloadStrategy:
    """reload 路由边界测试"""

    @pytest.mark.asyncio
    async def test_reload_strategy_not_found(self, client):
        """POST /api/strategy/{id}/reload 策略不存在返回 404"""
        response = await client.post("/api/strategy/99999/reload")
        assert response.status_code == 404
        assert "不存在" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_reload_strategy_not_active(self, client):
        """POST /api/strategy/{id}/reload 非 active 策略返回 400"""
        payload = {
            "code": "RELOAD_DRAFT",
            "name": "reload草稿策略",
            "strategy_type": "trend",
            "status": "draft",
            "code_content": "def initialize(c): pass\ndef handle_data(c, d): pass",
        }
        create = await client.post("/api/strategy/create", json=payload)
        assert create.status_code == 200
        sid = create.json()["data"]["id"]

        response = await client.post(f"/api/strategy/{sid}/reload")
        assert response.status_code == 400
        assert "active" in response.json()["detail"].lower() or "启用" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_reload_strategy_empty_code(self, client):
        """POST /api/strategy/{id}/reload 代码为空的 active 策略返回 400"""
        payload = {
            "code": "RELOAD_EMPTY",
            "name": "reload空代码策略",
            "strategy_type": "trend",
            "status": "active",
            "code_content": "def initialize(c): pass\ndef handle_data(c, d): pass",
        }
        create = await client.post("/api/strategy/create", json=payload)
        assert create.status_code == 200
        sid = create.json()["data"]["id"]

        # 把 code_content 清空
        update = await client.put(f"/api/strategy/{sid}", json={"code_content": ""})
        assert update.status_code == 200

        response = await client.post(f"/api/strategy/{sid}/reload")
        assert response.status_code == 400
        assert "代码" in response.json()["detail"] or "empty" in response.json()["detail"].lower()
