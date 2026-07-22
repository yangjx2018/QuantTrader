from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, computed_field, field_validator


class ExecutionBase(BaseModel):
    strategy_id: int
    strategy_name: str
    account_id: int
    params: Optional[dict] = None
    remark: Optional[str] = None

    @field_validator("strategy_id", mode="before")
    @classmethod
    def coerce_strategy_id(cls, v: Any) -> int:
        return int(v)

    @field_validator("account_id", mode="before")
    @classmethod
    def coerce_account_id(cls, v: Any) -> int:
        # 历史脏数据可能把 account_code 写入 account_id；列表接口不能因此 500
        if isinstance(v, bool):
            raise ValueError("invalid account_id")
        if isinstance(v, int):
            return v
        if v is None:
            raise ValueError("account_id required")
        text = str(v).strip()
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text)
        return 0


class ExecutionCreate(ExecutionBase):
    pass


class ExecutionUpdate(BaseModel):
    status: Optional[str] = None
    end_time: Optional[datetime] = None
    total_pnl: Optional[float] = None
    total_signals: Optional[int] = None
    total_orders: Optional[int] = None
    remark: Optional[str] = None


class Execution(ExecutionBase):
    id: int
    status: str
    start_time: datetime
    end_time: Optional[datetime] = None
    total_pnl: float = 0.0
    total_signals: int = 0
    total_orders: int = 0
    # DB 表暂无时间戳列，序列化时用 start_time/end_time 兜底
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @computed_field
    @property
    def started_at(self) -> str:
        return self.start_time.strftime("%Y-%m-%d %H:%M:%S")

    @computed_field
    @property
    def stopped_at(self) -> Optional[str]:
        return self.end_time.strftime("%Y-%m-%d %H:%M:%S") if self.end_time else None

    @computed_field
    @property
    def pnl(self) -> float:
        return self.total_pnl

    @computed_field
    @property
    def signals_count(self) -> int:
        return self.total_signals

    @computed_field
    @property
    def risk_alerts_count(self) -> int:
        return 0

    @computed_field
    @property
    def current_drawdown(self) -> float:
        return 0.0

    def model_post_init(self, __context: Any) -> None:
        if self.created_at is None:
            object.__setattr__(self, "created_at", self.start_time)
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", self.end_time or self.start_time)

    class Config:
        from_attributes = True


class ExecutionSignalBase(BaseModel):
    execution_id: int
    strategy_id: int
    symbol: str
    symbol_name: Optional[str] = None
    direction: str
    signal_price: float
    quantity: int
    order_type: str = "limit"
    reason: Optional[str] = None

    @field_validator("strategy_id", mode="before")
    @classmethod
    def coerce_signal_strategy_id(cls, v: Any) -> int:
        return int(v)


class ExecutionSignalCreate(ExecutionSignalBase):
    pass


class ExecutionSignalUpdate(BaseModel):
    risk_passed: Optional[bool] = None
    risk_reason: Optional[str] = None
    order_id: Optional[str] = None
    order_status: Optional[str] = None
    filled_price: Optional[float] = None
    filled_quantity: Optional[int] = None
    pnl: Optional[float] = None


class ExecutionSignal(ExecutionSignalBase):
    id: int
    risk_passed: bool = True
    risk_reason: Optional[str] = None
    order_id: Optional[str] = None
    order_status: Optional[str] = None
    filled_price: Optional[float] = None
    filled_quantity: Optional[int] = None
    pnl: Optional[float] = None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def signal_id(self) -> str:
        return f"SIG{self.id:06d}"

    @computed_field
    @property
    def side(self) -> str:
        return self.direction

    @computed_field
    @property
    def signal_type(self) -> str:
        return "entry" if self.direction == "buy" else "exit"

    @computed_field
    @property
    def suggested_price(self) -> float:
        return self.signal_price

    @computed_field
    @property
    def suggested_quantity(self) -> int:
        return self.quantity

    @computed_field
    @property
    def risk_rejection_reason(self) -> Optional[str]:
        return self.risk_reason

    @computed_field
    @property
    def order_submitted(self) -> bool:
        return self.order_id is not None

    class Config:
        from_attributes = True


class RiskRuleBase(BaseModel):
    rule_type: str
    rule_name: str
    enabled: bool = True
    params: dict
    description: Optional[str] = None


class RiskRuleCreate(RiskRuleBase):
    pass


class RiskRuleUpdate(BaseModel):
    rule_name: Optional[str] = None
    enabled: Optional[bool] = None
    params: Optional[dict] = None
    description: Optional[str] = None


class RiskRule(RiskRuleBase):
    id: int
    strategy_id: Optional[int] = None
    action: str = "alert_only"
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def name(self) -> str:
        return self.rule_name

    @computed_field
    @property
    def threshold_json(self) -> dict:
        return self.params

    class Config:
        from_attributes = True


class RiskAlertBase(BaseModel):
    execution_id: Optional[int] = None
    rule_id: Optional[int] = None
    rule_type: str
    severity: str
    title: str
    message: str


class RiskAlertCreate(RiskAlertBase):
    pass


class RiskAlertUpdate(BaseModel):
    acknowledged: Optional[bool] = None
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    action_taken: Optional[str] = None
    action_result: Optional[str] = None


class RiskAlert(RiskAlertBase):
    id: int
    strategy_name: Optional[str] = None
    triggered_value: Optional[float] = None
    threshold_value: Optional[float] = None
    acknowledged: bool = False
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    action_taken: Optional[str] = None
    action_result: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def level(self) -> str:
        return self.severity

    @computed_field
    @property
    def triggered_at(self) -> str:
        return self.created_at.strftime("%Y-%m-%d %H:%M:%S")

    class Config:
        from_attributes = True


class ExecutionLogBase(BaseModel):
    execution_id: int
    level: str = "info"
    category: str
    message: str
    details: Optional[dict] = None


class ExecutionLogCreate(ExecutionLogBase):
    pass


class ExecutionLog(ExecutionLogBase):
    id: int
    created_at: datetime

    @computed_field
    @property
    def timestamp(self) -> str:
        return self.created_at.strftime("%Y-%m-%d %H:%M:%S")

    @computed_field
    @property
    def data_json(self) -> Optional[dict]:
        return self.details

    class Config:
        from_attributes = True


class StartExecutionRequest(BaseModel):
    strategy_id: int
    account_id: int
    params: Optional[dict] = None  # 至少含 symbol，可选 timeframe/interval_sec


class ExecutionStatusResponse(BaseModel):
    running_count: int
    paused_count: int
    stopped_count: int
    today_pnl: float
    active_alerts: int
    total_signals_today: int


class MockKlineData(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
