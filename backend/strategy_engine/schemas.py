"""strategy_engine 模块的 Pydantic 请求/响应 Schema。

变更说明（vs 旧版）：
- 删除 entry_rules / exit_rules / risk_rules 字段（DSL 全在 code_content）
- 新增 code_content 字段（当前生效代码）
- code 字段加正则约束 ^[A-Z][A-Z0-9_]{2,63}$（与 design.md §5 一致）
- 新增 StrategyOption / StrategyValidateRequest / DryRunRequest 等为后续 router 用
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ============================================================
# 策略主档
# ============================================================

CODE_PATTERN = r"^[A-Z][A-Z0-9_]{2,63}$"


class StrategyBase(BaseModel):
    """策略基础字段（创建/响应共享）。"""

    code: str = Field(..., pattern=CODE_PATTERN, max_length=64, description="策略编码（大写字母开头+数字/下划线，3~64 字符）")
    name: str = Field(..., min_length=1, max_length=128, description="策略名称")
    strategy_type: str = Field(..., max_length=32, description="策略类型：trend/mean_reversion/arbitrage/sentiment")
    description: Optional[str] = Field(None, description="策略描述")
    status: str = Field(default="draft", max_length=16, description="状态：draft/active/archived")
    version: str = Field(default="1.0.0", max_length=32, description="当前版本号")
    code_content: Optional[str] = Field(None, max_length=50000, description="当前生效的 Python 策略代码")
    parameters: Optional[dict] = Field(None, description="默认参数")
    tags: Optional[list[str]] = None
    author: Optional[str] = Field(None, max_length=64)
    is_default: bool = False


class StrategyCreate(StrategyBase):
    """创建策略请求。"""


class StrategyUpdate(BaseModel):
    """更新策略请求（部分更新）。"""

    name: Optional[str] = Field(None, min_length=1, max_length=128)
    strategy_type: Optional[str] = Field(None, max_length=32)
    description: Optional[str] = None
    status: Optional[str] = Field(None, max_length=16)
    version: Optional[str] = Field(None, max_length=32)
    code_content: Optional[str] = Field(None, max_length=50000)
    parameters: Optional[dict] = None
    tags: Optional[list[str]] = None
    author: Optional[str] = Field(None, max_length=64)
    is_default: Optional[bool] = None


class StrategyResponse(StrategyBase):
    """策略响应。"""

    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 策略版本
# ============================================================

class StrategyVersionBase(BaseModel):
    strategy_id: Optional[int] = None  # 从 URL 路径获取，不在 request body 中
    version: str = Field(..., max_length=32)
    change_log: Optional[str] = None
    code_content: Optional[str] = None
    parameters: Optional[dict] = None
    status: str = Field(default="active", max_length=16)
    backtest_result: Optional[dict] = None


class StrategyVersionCreate(StrategyVersionBase):
    pass


class StrategyVersionResponse(StrategyVersionBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 简化列表（给 history_replay 等模块调用）
# ============================================================

class StrategyOption(BaseModel):
    """策略简化字段，用于下拉框填充。"""

    id: int
    name: str
    description: Optional[str] = None
    strategy_type: Optional[str] = None


# ============================================================
# 校验与试运行
# ============================================================

class StrategyValidateRequest(BaseModel):
    """策略代码语法 + 沙箱加载校验请求。"""

    code_content: str = Field(..., min_length=1, max_length=50000)
    parameters: Optional[dict] = None


class ValidationIssue(BaseModel):
    """校验问题项。"""

    line: Optional[int] = None
    column: Optional[int] = None
    severity: str  # "error" / "warning"
    code: str      # "SYNTAX_ERROR" / "FORBIDDEN_IMPORT" / "MISSING_HOOK" / ...
    message: str


class StrategyValidateResponse(BaseModel):
    """策略校验响应。"""

    valid: bool
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []


class DryRunRequest(BaseModel):
    """策略试运行请求。"""

    stock_code: str = Field(..., pattern=r"^\d{6}\.[SZSH]+$", description="股票代码")
    start_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="起始日期 YYYY-MM-DD")
    end_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="结束日期 YYYY-MM-DD")
    parameters: Optional[dict] = None
    max_bars: int = Field(30, ge=1, le=100, description="最大 bar 数")


class DryRunBarSummary(BaseModel):
    """试运行 bar 简化结构（仅核心字段）。"""

    time: str
    close: float
    total_assets: float
    signal: Optional[str] = None
    orders_count: int = 0


class DryRunResponse(BaseModel):
    """试运行响应。"""

    session_id: str
    total_bars: int
    time_elapsed: float
    final_capital: float
    total_return_pct: float
    bars: list[DryRunBarSummary]


# ============================================================
# P3 新增：并发回测
# ============================================================

class BatchBacktestRequest(BaseModel):
    """并发回测请求。"""

    strategy_ids: list[int] = Field(..., min_length=1, max_length=10, description="策略 ID 列表")
    stock_code: str = Field(..., max_length=20, description="股票代码")
    start_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="起始日期")
    end_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="结束日期")
    timeframe: str = Field("1d", description="时间框架")


class BatchBacktestResponse(BaseModel):
    """并发回测响应。"""

    results: list[dict]


# ============================================================
# P3 新增：版本对比
# ============================================================

class CompareRequest(BaseModel):
    """版本对比请求。"""

    version_ids: list[int] = Field(..., min_length=2, max_length=2, description="版本 ID 列表（必须 2 个）")
    stock_code: str = Field(..., max_length=20, description="股票代码")
    start_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="起始日期")
    end_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="结束日期")
    timeframe: str = Field("1d", description="时间框架")


class CompareResponse(BaseModel):
    """版本对比响应。"""

    version_1: dict
    version_2: dict
    diff: dict


# ============================================================
# P3 新增：策略导出/导入
# ============================================================

class ExportRequest(BaseModel):
    """策略导出请求。"""

    strategy_ids: list[int] = Field(..., min_length=1, description="策略 ID 列表")
    include_versions: bool = Field(False, description="是否包含版本历史")


class ExportResponse(BaseModel):
    """策略导出响应。"""

    strategies: list[dict]


class StrategyImportItem(BaseModel):
    """单个策略导入数据。"""

    code: str = Field(..., max_length=64, description="策略编码")
    name: str = Field(..., max_length=128, description="策略名称")
    strategy_type: str = Field(..., max_length=32, description="策略类型")
    description: Optional[str] = None
    code_content: str = Field(..., description="策略代码")
    parameters: Optional[dict] = None
    version: Optional[str] = Field("1.0.0", max_length=32, description="版本号")
    versions: Optional[list[dict]] = None


class ImportRequest(BaseModel):
    """策略导入请求。"""

    strategies: list[StrategyImportItem] = Field(..., min_length=1, description="策略数据列表")


class ImportResponse(BaseModel):
    """策略导入响应。"""

    imported: int
    skipped: int
    skipped_codes: list[str] = []


__all__ = [
    "StrategyBase",
    "StrategyCreate",
    "StrategyUpdate",
    "StrategyResponse",
    "StrategyVersionBase",
    "StrategyVersionCreate",
    "StrategyVersionResponse",
    "StrategyOption",
    "StrategyValidateRequest",
    "ValidationIssue",
    "StrategyValidateResponse",
    "DryRunRequest",
    "DryRunBarSummary",
    "DryRunResponse",
    # P3 新增
    "BatchBacktestRequest",
    "BatchBacktestResponse",
    "CompareRequest",
    "CompareResponse",
    "ExportRequest",
    "ExportResponse",
    "ImportRequest",
    "ImportResponse",
]
