from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text, ForeignKey, JSON
from sqlalchemy.orm import relationship
from common.database import Base, TimestampMixin


class Execution(Base):
    """策略执行实例"""

    __tablename__ = "execution"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(String(64), nullable=False, index=True)
    strategy_name = Column(String(100), nullable=False)
    account_id = Column(Integer, nullable=False, comment="账户ID（关联 account_trading.trading_account）")
    status = Column(String(20), nullable=False, default="running")
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    total_pnl = Column(Float, default=0.0)
    total_signals = Column(Integer, default=0)
    total_orders = Column(Integer, default=0)
    params = Column(JSON, nullable=True)
    remark = Column(String(255), nullable=True)

    signals = relationship("ExecutionSignal", back_populates="execution", cascade="all, delete-orphan")
    alerts = relationship("RiskAlert", back_populates="execution", cascade="all, delete-orphan")
    logs = relationship("ExecutionLog", back_populates="execution", cascade="all, delete-orphan")


class ExecutionSignal(Base):
    """交易信号记录"""

    __tablename__ = "execution_signal"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(Integer, ForeignKey("execution.id"), nullable=False, index=True)
    strategy_id = Column(String(64), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    symbol_name = Column(String(50), nullable=True)
    direction = Column(String(10), nullable=False)
    signal_price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    order_type = Column(String(20), default="limit")
    reason = Column(String(255), nullable=True)
    risk_passed = Column(Boolean, default=True)
    risk_reason = Column(String(255), nullable=True)
    order_id = Column(String(64), nullable=True)
    order_status = Column(String(20), nullable=True)
    filled_price = Column(Float, nullable=True)
    filled_quantity = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    pnl = Column(Float, nullable=True)

    execution = relationship("Execution", back_populates="signals")


class RiskRule(Base, TimestampMixin):
    """风控规则配置"""

    __tablename__ = "risk_rule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_type = Column(String(50), nullable=False, index=True)
    rule_name = Column(String(100), nullable=False)
    strategy_id = Column(Integer, nullable=True, index=True)
    enabled = Column(Boolean, default=True)
    params = Column(JSON, nullable=False)
    action = Column(String(50), default="alert_only")
    description = Column(String(255), nullable=True)


class RiskAlert(Base, TimestampMixin):
    """风控告警记录"""

    __tablename__ = "risk_alert"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(Integer, ForeignKey("execution.id"), nullable=True, index=True)
    rule_id = Column(Integer, nullable=True)
    rule_type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    title = Column(String(100), nullable=False)
    message = Column(Text, nullable=False)
    acknowledged = Column(Boolean, default=False)
    acknowledged_at = Column(DateTime, nullable=True)
    acknowledged_by = Column(String(50), nullable=True)
    action_taken = Column(String(50), nullable=True)
    action_result = Column(Text, nullable=True)

    execution = relationship("Execution", back_populates="alerts")


class ExecutionLog(Base, TimestampMixin):
    """执行操作日志"""

    __tablename__ = "execution_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(Integer, ForeignKey("execution.id"), nullable=False, index=True)
    level = Column(String(20), nullable=False, default="info")
    category = Column(String(50), nullable=False)
    message = Column(Text, nullable=False)
    details = Column(JSON, nullable=True)

    execution = relationship("Execution", back_populates="logs")
