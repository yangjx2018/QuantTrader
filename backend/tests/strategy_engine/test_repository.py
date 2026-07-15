"""repository.py 仓储层测试。

覆盖：
- 3.3: StrategyRepository 全部方法（create / get_by_id / get_by_code / list_all / update / delete / count / list_options）
- 3.4: StrategyVersionRepository 全部方法（create / get_by_id / list_by_strategy / get_latest / delete_by_strategy）
"""

import pytest
from datetime import datetime

from strategy_engine.repository import StrategyRepository, StrategyVersionRepository
from strategy_engine.models import Strategy, StrategyVersion


@pytest.fixture
def strategy_repo(test_db):
    """StrategyRepository fixture。"""
    return StrategyRepository(test_db)


@pytest.fixture
def version_repo(test_db):
    """StrategyVersionRepository fixture。"""
    return StrategyVersionRepository(test_db)


@pytest.fixture
async def sample_strategy(strategy_repo):
    """创建示例策略用于测试。"""
    import time
    unique_code = f"TEST_REPO_{int(time.time() * 1000)}"
    strategy_data = {
        "code": unique_code,
        "name": "测试策略",
        "strategy_type": "trend",
        "description": "用于仓储层测试",
        "status": "active",
        "version": "1.0.0",
        "code_content": "def initialize(context): pass\ndef handle_data(context, data): pass",
        "parameters": {"param1": "value1"},
        "tags": ["test", "repo"],
        "author": "test_user",
        "is_default": False,
    }
    strategy = await strategy_repo.create(strategy_data)
    yield strategy
    # 清理
    await strategy_repo.delete(strategy.id)


class TestStrategyRepository:
    """3.3: StrategyRepository 测试。"""

    @pytest.mark.asyncio
    async def test_create_success(self, strategy_repo):
        """create() 成功创建策略。"""
        import time
        unique_code = f"TEST_CREATE_{int(time.time() * 1000)}"
        strategy_data = {
            "code": unique_code,
            "name": "测试创建",
            "strategy_type": "trend",
            "description": "测试创建策略",
            "status": "draft",
            "version": "1.0.0",
            "code_content": "def initialize(context): pass",
            "parameters": {"test": True},
            "tags": ["create", "test"],
            "author": "test_user",
            "is_default": False,
        }
        strategy = await strategy_repo.create(strategy_data)

        assert strategy is not None
        assert strategy.id is not None
        assert strategy.code == unique_code
        assert strategy.name == "测试创建"
        assert strategy.strategy_type == "trend"
        assert strategy.status == "draft"
        assert strategy.parameters == {"test": True}
        assert strategy.tags == ["create", "test"]

        # 清理
        await strategy_repo.delete(strategy.id)

    @pytest.mark.asyncio
    async def test_get_by_id_success(self, strategy_repo, sample_strategy):
        """get_by_id() 成功获取策略。"""
        strategy = await strategy_repo.get_by_id(sample_strategy.id)

        assert strategy is not None
        assert strategy.id == sample_strategy.id
        assert strategy.code == sample_strategy.code
        assert strategy.name == sample_strategy.name

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self, strategy_repo):
        """get_by_id() 策略不存在返回 None。"""
        strategy = await strategy_repo.get_by_id(99999)
        assert strategy is None

    @pytest.mark.asyncio
    async def test_get_by_code_success(self, strategy_repo, sample_strategy):
        """get_by_code() 成功获取策略。"""
        strategy = await strategy_repo.get_by_code(sample_strategy.code)

        assert strategy is not None
        assert strategy.code == sample_strategy.code
        assert strategy.id == sample_strategy.id

    @pytest.mark.asyncio
    async def test_get_by_code_not_found(self, strategy_repo):
        """get_by_code() 策略不存在返回 None。"""
        strategy = await strategy_repo.get_by_code("NON_EXISTENT_CODE_99999")
        assert strategy is None

    @pytest.mark.asyncio
    async def test_list_all_default(self, strategy_repo, sample_strategy):
        """list_all() 默认返回所有策略。"""
        strategies = await strategy_repo.list_all()

        assert isinstance(strategies, list)
        assert len(strategies) >= 1  # 至少有 sample_strategy
        # 验证包含 sample_strategy
        codes = [s.code for s in strategies]
        assert sample_strategy.code in codes

    @pytest.mark.asyncio
    async def test_list_all_with_status_filter(self, strategy_repo, sample_strategy):
        """list_all(status='active') 过滤 active 策略。"""
        strategies = await strategy_repo.list_all(status="active")

        assert isinstance(strategies, list)
        # 所有返回的策略都应该是 active
        for strategy in strategies:
            assert strategy.status == "active"

    @pytest.mark.asyncio
    async def test_list_all_with_type_filter(self, strategy_repo, sample_strategy):
        """list_all(strategy_type='trend') 过滤 trend 策略。"""
        strategies = await strategy_repo.list_all(strategy_type="trend")

        assert isinstance(strategies, list)
        # 所有返回的策略都应该是 trend
        for strategy in strategies:
            assert strategy.strategy_type == "trend"

    @pytest.mark.asyncio
    async def test_list_all_with_pagination(self, strategy_repo, sample_strategy):
        """list_all(limit=1, offset=0) 分页。"""
        strategies = await strategy_repo.list_all(limit=1, offset=0)

        assert isinstance(strategies, list)
        assert len(strategies) <= 1

    @pytest.mark.asyncio
    async def test_update_success(self, strategy_repo, sample_strategy):
        """update() 成功更新策略。"""
        update_data = {
            "name": "更新后的策略名称",
            "description": "更新后的描述",
            "status": "archived",
        }
        updated = await strategy_repo.update(sample_strategy.id, update_data)

        assert updated is not None
        assert updated.id == sample_strategy.id
        assert updated.name == "更新后的策略名称"
        assert updated.description == "更新后的描述"
        assert updated.status == "archived"

    @pytest.mark.asyncio
    async def test_update_partial(self, strategy_repo, sample_strategy):
        """update() 部分更新只修改指定字段。"""
        original_name = sample_strategy.name
        update_data = {
            "description": "只更新描述",
        }
        updated = await strategy_repo.update(sample_strategy.id, update_data)

        assert updated is not None
        assert updated.name == original_name  # 名称未变
        assert updated.description == "只更新描述"  # 描述已更新

    @pytest.mark.asyncio
    async def test_update_not_found(self, strategy_repo):
        """update() 策略不存在返回 None。"""
        update_data = {"name": "test"}
        updated = await strategy_repo.update(99999, update_data)
        assert updated is None

    @pytest.mark.asyncio
    async def test_delete_success(self, strategy_repo):
        """delete() 成功删除策略。"""
        # 先创建
        import time
        unique_code = f"TEST_DELETE_{int(time.time() * 1000)}"
        strategy_data = {
            "code": unique_code,
            "name": "测试删除",
            "strategy_type": "trend",
            "status": "draft",
            "version": "1.0.0",
        }
        strategy = await strategy_repo.create(strategy_data)
        strategy_id = strategy.id

        # 删除
        result = await strategy_repo.delete(strategy_id)
        assert result is True

        # 验证已删除
        deleted = await strategy_repo.get_by_id(strategy_id)
        assert deleted is None

    @pytest.mark.asyncio
    async def test_delete_not_found(self, strategy_repo):
        """delete() 策略不存在返回 False。"""
        result = await strategy_repo.delete(99999)
        assert result is False

    @pytest.mark.asyncio
    async def test_count_default(self, strategy_repo, sample_strategy):
        """count() 默认返回所有策略数量。"""
        count = await strategy_repo.count()
        assert isinstance(count, int)
        assert count >= 1  # 至少有 sample_strategy

    @pytest.mark.asyncio
    async def test_count_with_status_filter(self, strategy_repo, sample_strategy):
        """count(status='active') 返回 active 策略数量。"""
        count = await strategy_repo.count(status="active")
        assert isinstance(count, int)
        assert count >= 1  # sample_strategy 是 active

    @pytest.mark.asyncio
    async def test_list_options_default(self, strategy_repo, sample_strategy):
        """list_options() 默认返回 active 策略的简化列表。"""
        options = await strategy_repo.list_options()

        assert isinstance(options, list)
        assert len(options) >= 1
        # 验证返回的是简化字段
        for option in options:
            assert hasattr(option, "id")
            assert hasattr(option, "name")
            assert hasattr(option, "description")
            assert hasattr(option, "strategy_type")
            assert option.status == "active"  # 默认只返回 active

    @pytest.mark.asyncio
    async def test_list_options_all(self, strategy_repo, sample_strategy):
        """list_options(status=None) 返回所有策略。"""
        options = await strategy_repo.list_options(status=None)

        assert isinstance(options, list)
        assert len(options) >= 1


class TestStrategyVersionRepository:
    """3.4: StrategyVersionRepository 测试。"""

    @pytest.mark.asyncio
    async def test_create_success(self, version_repo, sample_strategy):
        """create() 成功创建版本。"""
        import time
        unique_version = f"2.0.{int(time.time())}"
        version_data = {
            "strategy_id": sample_strategy.id,
            "version": unique_version,
            "code_content": "def initialize(context): pass\ndef handle_data(context, data): pass",
            "change_log": "测试版本创建",
            "parameters": {"new_param": "new_value"},
            "status": "active",
        }
        version = await version_repo.create(version_data)

        assert version is not None
        assert version.id is not None
        assert version.strategy_id == sample_strategy.id
        assert version.version == unique_version
        assert version.code_content is not None
        assert version.change_log == "测试版本创建"

    @pytest.mark.asyncio
    async def test_get_by_id_success(self, version_repo, sample_strategy):
        """get_by_id() 成功获取版本。"""
        # 先创建版本
        import time
        unique_version = f"3.0.{int(time.time())}"
        version_data = {
            "strategy_id": sample_strategy.id,
            "version": unique_version,
            "code_content": "def initialize(context): pass",
        }
        created = await version_repo.create(version_data)

        # 获取
        version = await version_repo.get_by_id(created.id)

        assert version is not None
        assert version.id == created.id
        assert version.version == unique_version

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self, version_repo):
        """get_by_id() 版本不存在返回 None。"""
        version = await version_repo.get_by_id(99999)
        assert version is None

    @pytest.mark.asyncio
    async def test_list_by_strategy_default(self, version_repo, sample_strategy):
        """list_by_strategy() 默认返回策略的所有版本。"""
        # 创建多个版本
        import time
        for i in range(3):
            version_data = {
                "strategy_id": sample_strategy.id,
                "version": f"4.{i}.{int(time.time())}",
                "code_content": f"def initialize(context): pass  # v{i}",
            }
            await version_repo.create(version_data)

        # 获取列表
        versions = await version_repo.list_by_strategy(sample_strategy.id)

        assert isinstance(versions, list)
        assert len(versions) >= 3
        # 验证所有版本都属于该策略
        for version in versions:
            assert version.strategy_id == sample_strategy.id

    @pytest.mark.asyncio
    async def test_list_by_strategy_with_status_filter(self, version_repo, sample_strategy):
        """list_by_strategy(status='active') 过滤 active 版本。"""
        # 创建 active 和 archived 版本
        import time
        ts = int(time.time())
        await version_repo.create({
            "strategy_id": sample_strategy.id,
            "version": f"5.0.{ts}",
            "code_content": "def initialize(context): pass",
            "status": "active",
        })
        await version_repo.create({
            "strategy_id": sample_strategy.id,
            "version": f"5.1.{ts}",
            "code_content": "def initialize(context): pass",
            "status": "archived",
        })

        # 获取 active 版本
        versions = await version_repo.list_by_strategy(sample_strategy.id, status="active")

        assert isinstance(versions, list)
        # 所有返回的版本都应该是 active
        for version in versions:
            assert version.status == "active"

    @pytest.mark.asyncio
    async def test_list_by_strategy_not_found(self, version_repo):
        """list_by_strategy() 策略不存在返回空列表。"""
        versions = await version_repo.list_by_strategy(99999)
        assert isinstance(versions, list)
        assert len(versions) == 0

    @pytest.mark.asyncio
    async def test_get_latest_success(self, version_repo, sample_strategy):
        """get_latest() 成功获取最新版本。"""
        # 创建多个版本
        import time
        ts = int(time.time())
        for i in range(3):
            await version_repo.create({
                "strategy_id": sample_strategy.id,
                "version": f"6.{i}.{ts + i}",
                "code_content": f"def initialize(context): pass  # v{i}",
            })

        # 获取最新版本
        latest = await version_repo.get_latest(sample_strategy.id)

        assert latest is not None
        assert latest.strategy_id == sample_strategy.id
        # 应该是最新创建的版本（按 created_at 排序）

    @pytest.mark.asyncio
    async def test_get_latest_not_found(self, version_repo):
        """get_latest() 策略不存在返回 None。"""
        latest = await version_repo.get_latest(99999)
        assert latest is None

    @pytest.mark.asyncio
    async def test_delete_by_strategy_success(self, version_repo, sample_strategy):
        """delete_by_strategy() 成功删除策略的所有版本。"""
        # 创建多个版本
        import time
        ts = int(time.time())
        for i in range(3):
            await version_repo.create({
                "strategy_id": sample_strategy.id,
                "version": f"7.{i}.{ts + i}",
                "code_content": f"def initialize(context): pass  # v{i}",
            })

        # 删除所有版本
        count = await version_repo.delete_by_strategy(sample_strategy.id)

        assert count == 3

        # 验证已删除
        versions = await version_repo.list_by_strategy(sample_strategy.id)
        assert len(versions) == 0

    @pytest.mark.asyncio
    async def test_delete_by_strategy_not_found(self, version_repo):
        """delete_by_strategy() 策略不存在返回 0。"""
        count = await version_repo.delete_by_strategy(99999)
        assert count == 0
