"""strategy_engine.runtime 单元测试。

覆盖：
- sandbox: 白名单 import / 危险 builtin 拦截
- loader: 正常加载 / 缺钩子 / import 拦截 / 沙箱逃逸尝试
- context: Portfolio 撮合计算
- api: order_* 数量取整、target 计算
"""

import pytest

from strategy_engine.runtime.context import Portfolio, Position, StrategyContext
from strategy_engine.runtime.sandbox import (
    ALLOWED_IMPORTS,
    FORBIDDEN_BUILTINS,
    build_safe_globals,
)
from strategy_engine.runtime.loader import (
    StrategyLoader,
    StrategyLoadError,
    InvalidStrategyError,
    validate_code,
)
from strategy_engine.runtime.api import DSLLogger, make_order_api


# ============================================================
# Sandbox 测试
# ============================================================

class TestSandbox:
    def test_allowed_imports_whitelist(self):
        assert "math" in ALLOWED_IMPORTS
        assert "statistics" in ALLOWED_IMPORTS
        assert "datetime" in ALLOWED_IMPORTS
        assert "os" not in ALLOWED_IMPORTS
        assert "subprocess" not in ALLOWED_IMPORTS
        assert "sys" not in ALLOWED_IMPORTS

    def test_forbidden_builtins_blacklist(self):
        assert "open" in FORBIDDEN_BUILTINS
        assert "eval" in FORBIDDEN_BUILTINS
        assert "exec" in FORBIDDEN_BUILTINS
        assert "__import__" in FORBIDDEN_BUILTINS

    def test_safe_globals_has_no_open(self):
        g = build_safe_globals({})
        assert "open" not in g["__builtins__"]
        assert "eval" not in g["__builtins__"]
        assert "exec" not in g["__builtins__"]

    def test_safe_globals_import_restricted(self):
        """沙箱内 import os 应被拦截。"""
        code = "import os\nos.getcwd()"
        with pytest.raises(ImportError):
            exec(compile(code, "<test>", "exec"), build_safe_globals({}))

    def test_safe_globals_import_math_ok(self):
        """沙箱内 import math 应通过。"""
        code = "import math\nresult = math.sqrt(4)"
        g = build_safe_globals({})
        exec(compile(code, "<test>", "exec"), g)
        assert g["result"] == 2.0

    def test_safe_globals_builtins_is_dict_not_module(self):
        """__builtins__ 应为 dict 而非 module，防止反向获取。"""
        g = build_safe_globals({})
        assert isinstance(g["__builtins__"], dict)


# ============================================================
# Loader 测试
# ============================================================

class TestLoader:
    def test_load_valid_strategy(self):
        code = """
def initialize(context):
    pass

def handle_data(context, data):
    pass
"""
        loader = StrategyLoader()
        instance = loader.load(code)
        assert instance.has_hook("initialize")
        assert instance.has_hook("handle_data")
        assert not instance.has_hook("before_trading_start")
        assert not instance.has_hook("control_risk")

    def test_load_missing_handle_data(self):
        code = """
def initialize(context):
    pass
"""
        loader = StrategyLoader()
        with pytest.raises(InvalidStrategyError) as exc_info:
            loader.load(code)
        assert "handle_data" in str(exc_info.value)

    def test_load_missing_initialize(self):
        code = """
def handle_data(context, data):
    pass
"""
        loader = StrategyLoader()
        with pytest.raises(InvalidStrategyError):
            loader.load(code)

    def test_load_empty_code(self):
        loader = StrategyLoader()
        with pytest.raises(StrategyLoadError):
            loader.load("")

    def test_load_syntax_error(self):
        code = "def initialize(context\n    pass"  # 缺冒号
        loader = StrategyLoader()
        with pytest.raises(StrategyLoadError) as exc_info:
            loader.load(code)
        assert exc_info.value.line is not None

    def test_load_import_os_blocked(self):
        code = """
import os

def initialize(context):
    pass

def handle_data(context, data):
    os.getcwd()
"""
        loader = StrategyLoader()
        with pytest.raises(StrategyLoadError) as exc_info:
            loader.load(code)
        # 沙箱 import 拦截被翻译为 StrategyLoadError
        assert "forbidden" in str(exc_info.value).lower() or "import" in str(exc_info.value).lower()

    def test_load_call_initialize(self):
        """initialize 钩子可被调用并接收 context。"""
        code = """
g.initialized = False

def initialize(context):
    g.initialized = True

def handle_data(context, data):
    pass
"""
        loader = StrategyLoader()
        instance = loader.load(code)
        portfolio = Portfolio(initial_capital=100000, cash=100000)
        ctx = StrategyContext(portfolio=portfolio)
        instance.call("initialize", ctx)
        assert instance.globals_dict["g"].initialized is True

    def test_load_with_parameters_injected_to_g(self):
        code = """
def initialize(context):
    g.checked = g.window + 1

def handle_data(context, data):
    pass
"""
        loader = StrategyLoader()
        instance = loader.load(code, parameters={"window": 5})
        portfolio = Portfolio(initial_capital=100000, cash=100000)
        ctx = StrategyContext(portfolio=portfolio)
        instance.call("initialize", ctx)
        assert instance.globals_dict["g"].checked == 6


# ============================================================
# validate_code 测试
# ============================================================

class TestValidateCode:
    def test_validate_valid_strategy(self):
        code = """
def initialize(context):
    pass

def handle_data(context, data):
    pass
"""
        result = validate_code(code)
        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_validate_syntax_error(self):
        code = "def initialize(context\n    pass"
        result = validate_code(code)
        assert result["valid"] is False
        assert result["errors"][0]["code"] == "SYNTAX_ERROR"

    def test_validate_missing_hook_is_warning(self):
        code = "x = 1"
        result = validate_code(code)
        assert result["valid"] is True
        assert any(w["code"] == "MISSING_HOOK" for w in result["warnings"])

    def test_validate_forbidden_import(self):
        code = """
import os
def initialize(context):
    pass
def handle_data(context, data):
    pass
"""
        result = validate_code(code)
        assert result["valid"] is False
        assert result["errors"][0]["code"] == "LOAD_ERROR"


# ============================================================
# Context / Portfolio 测试
# ============================================================

class TestPortfolio:
    def test_total_value_empty(self):
        p = Portfolio(initial_capital=100000, cash=100000)
        assert p.total_value == 100000

    def test_total_value_with_position(self):
        p = Portfolio(initial_capital=100000, cash=50000)
        p.positions["600000.SH"] = Position(
            security="600000.SH", quantity=1000, cost_price=50, current_price=50
        )
        assert p.total_value == 100000  # 50000 + 1000*50

    def test_apply_buy_creates_position(self):
        p = Portfolio(initial_capital=100000, cash=100000, commission_rate=0.001)
        p._apply_buy("600000.SH", 1000, 10.0, commission=10.0)
        assert p.cash == pytest.approx(100000 - 10000 - 10)
        assert p.positions["600000.SH"].quantity == 1000
        assert p.positions["600000.SH"].cost_price == 10.0

    def test_apply_buy_merges_position_with_weighted_cost(self):
        p = Portfolio(initial_capital=100000, cash=100000, commission_rate=0.001)
        p._apply_buy("600000.SH", 1000, 10.0, commission=10.0)
        p._apply_buy("600000.SH", 1000, 12.0, commission=12.0)
        # 加权成本 (10*1000 + 12*1000) / 2000 = 11
        assert p.positions["600000.SH"].quantity == 2000
        assert p.positions["600000.SH"].cost_price == 11.0

    def test_apply_sell_realizes_pnl(self):
        p = Portfolio(initial_capital=100000, cash=100000, commission_rate=0.001)
        p._apply_buy("600000.SH", 1000, 10.0, commission=10.0)
        pnl = p._apply_sell("600000.SH", 1000, 12.0, commission=12.0)
        # pnl = (12 - 10) * 1000 - 12 = 1988
        assert pnl == pytest.approx(1988, rel=0.01)
        assert "600000.SH" not in p.positions

    def test_apply_buy_insufficient_cash_raises(self):
        p = Portfolio(initial_capital=100, cash=100, commission_rate=0.001)
        with pytest.raises(ValueError):
            p._apply_buy("600000.SH", 1000, 10.0, commission=10.0)


# ============================================================
# Order API 测试
# ============================================================

class TestOrderAPI:
    def _make_ctx(self):
        portfolio = Portfolio(initial_capital=100000, cash=100000)
        return StrategyContext(portfolio=portfolio)

    def test_order_buy_appends_pending(self):
        ctx = self._make_ctx()
        log = DSLLogger(sink=[])
        api = make_order_api(ctx, log)
        api["order"]("600000.SH", 1000)
        assert len(ctx.pending_orders) == 1
        order = ctx.pending_orders[0]
        assert order["side"] == "buy"
        assert order["mode"] == "quantity"
        assert order["target"] == 1000.0

    def test_order_buy_rounded_to_100(self):
        ctx = self._make_ctx()
        log = DSLLogger(sink=[])
        api = make_order_api(ctx, log)
        api["order"]("600000.SH", 1050)
        assert ctx.pending_orders[0]["target"] == 1000.0

    def test_order_buy_below_100_skipped(self):
        ctx = self._make_ctx()
        log = DSLLogger(sink=[])
        api = make_order_api(ctx, log)
        api["order"]("600000.SH", 50)
        assert len(ctx.pending_orders) == 0

    def test_order_sell_negative_amount(self):
        ctx = self._make_ctx()
        log = DSLLogger(sink=[])
        api = make_order_api(ctx, log)
        api["order"]("600000.SH", -1000)
        assert ctx.pending_orders[0]["side"] == "sell"
        assert ctx.pending_orders[0]["target"] == 1000.0

    def test_order_value_buy(self):
        ctx = self._make_ctx()
        log = DSLLogger(sink=[])
        api = make_order_api(ctx, log)
        api["order_value"]("600000.SH", 30000)
        assert ctx.pending_orders[0]["side"] == "buy"
        assert ctx.pending_orders[0]["mode"] == "value"
        assert ctx.pending_orders[0]["target"] == 30000.0

    def test_order_target_zero(self):
        ctx = self._make_ctx()
        log = DSLLogger(sink=[])
        api = make_order_api(ctx, log)
        api["order_target"]("600000.SH", 0)
        assert ctx.pending_orders[0]["mode"] == "target_quantity"
        assert ctx.pending_orders[0]["target"] == 0.0

    def test_order_zero_skipped(self):
        ctx = self._make_ctx()
        log = DSLLogger(sink=[])
        api = make_order_api(ctx, log)
        api["order"]("600000.SH", 0)
        api["order_value"]("600000.SH", 0)
        assert len(ctx.pending_orders) == 0

    def test_order_target_value_buy(self):
        """order_target_value 写入 target_value 模式的 pending 订单。"""
        ctx = self._make_ctx()
        log = DSLLogger(sink=[])
        api = make_order_api(ctx, log)
        api["order_target_value"]("600000.SH", 50000)
        assert len(ctx.pending_orders) == 1
        order = ctx.pending_orders[0]
        assert order["mode"] == "target_value"
        assert order["target"] == 50000.0

    def test_order_target_value_negative_skipped(self):
        """order_target_value 目标市值为负数应被跳过。"""
        ctx = self._make_ctx()
        log = DSLLogger(sink=[])
        api = make_order_api(ctx, log)
        api["order_target_value"]("600000.SH", -1)
        assert len(ctx.pending_orders) == 0


# ============================================================
# DSLLogger 测试
# ============================================================

class TestDSLLogger:
    def test_logger_writes_to_sink(self):
        sink = []
        log = DSLLogger(sink=sink)
        log.info("buy signal")
        log.warn("cash low")
        log.error("div zero")
        assert sink == ["[INFO] buy signal", "[WARN] cash low", "[ERROR] div zero"]
