"""
历史回放模块 - 路由定义

本文件定义了历史回放模块的所有 HTTP 接口，职责：
  1. 声明 URL 路径、HTTP 方法、请求参数和路径参数
  2. 将请求参数从 HTTP 层解包，调用 service 层获取业务数据
  3. 将 service 返回的 Pydantic 模型序列化为统一响应格式 {"success": True, "data": ...}

设计原则：
  - router 不包含任何业务逻辑，仅做参数透传和响应包装
  - 所有业务逻辑在 service.py 中实现
  - 请求/响应的数据结构在 schemas.py 中定义
  - 统一响应格式便于前端 request.ts 中的拦截器统一处理

接口分三类：
  - 配置数据接口：为前端配置栏提供下拉选项数据
  - 回测控制接口：管理回测的生命周期（启动、暂停、继续、停止、调速）
  - 回测数据接口：获取回测产生的图表和表格数据
"""

from __future__ import annotations

from fastapi import APIRouter

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from account_trading.backtest_engine import BacktestBar, BacktestContext, BacktestOrderRequest, BacktestTradingEngine
from account_trading.backtest_engine.schemas import FeeRule, SlippageRule
from account_trading.models import BacktestRun, BacktestTrade
from common.dependencies import get_db
from common.utils.response import ApiResponse


from .schemas import (
    ReplayStartRequest,
    ReplayControlRequest,
    ReplaySpeedRequest,
    StockSearchRequest,
)
from .service import (
    search_stocks,
    list_strategies,
    list_virtual_accounts,
    start_replay,
    get_session,
    control_replay,
    set_replay_speed,
    get_kline_data,
    get_trade_signals,
    get_trade_records,
    get_metrics,
    get_equity_curve,
    get_benchmark_data,
    get_strategy_return_data,
    get_daily_pnl,
    get_daily_positions,
    get_replay_logs,
)

# 模块路由前缀 /api/replay，所有接口路径均以此为前缀
# tags 用于 FastAPI 自动生成的 API 文档分组
router = APIRouter(prefix="/api/replay", tags=["历史回放"])


# ============================================================
# 配置数据接口
# 为前端回测配置栏提供下拉选项和搜索数据，用户在启动回测前需要完成配置
# ============================================================

@router.post("/stocks/search")
async def search_stocks_api(req: StockSearchRequest):
    """
    搜索股票（代码/名称/拼音模糊匹配）

    业务角度：
      用户在配置栏的"股票代码"输入框中键入关键词时，前端实时调用此接口
      获取匹配的股票列表，供用户选择回测标的。输入框的交互方式为：
      键入 → 调接口 → 下拉展示候选 → 用户点选。

    技术角度：
      POST 而非 GET，因为 keyword 作为 JSON body 更灵活，未来可扩展搜索条件
      （如市场筛选、行业筛选等）。req.keyword 为用户输入的搜索关键词，
      req.limit 为返回数量上限（防止结果过多影响前端渲染性能）。

    参数：
      req.keyword (str): 搜索关键词，可以是股票代码（如 "000001"）、
                         股票名称（如 "平安"）或拼音首字母（如 "PAYH"）
      req.limit (int):   返回结果数量上限，默认 10

    返回值：
      {"success": True, "data": [StockInfo, ...]}
      data 为匹配到的股票信息列表，每项包含 code（代码）、name（名称）、
      market（市场）字段，供前端下拉列表渲染
    """
    data = await search_stocks(req.keyword, req.limit)
    return {"success": True, "data": [d.model_dump() for d in data]}


@router.get("/strategies")
async def list_strategies_api(db: AsyncSession = Depends(get_db)):
    """
    获取策略列表

    业务角度：
      用户在配置栏的"策略选择"下拉框中需要选择一个策略来驱动回测。
      此接口返回当前系统中所有可用的策略，供下拉框渲染选项。
      策略来源为 strategy_engine 模块（DB 表 strategy）。

    技术角度：
      GET 方法，无请求参数。返回的列表数据量通常较小（几十条策略），
      不需要分页。默认查 strategy_engine DB，env USE_MOCK_STRATEGY=true
      时回退到 4 个 mock 数据。

    参数：
      无

    返回值：
      {"success": True, "data": [StrategyInfo, ...]}
      data 为策略信息列表，每项包含 id（策略ID）、name（策略名称）、
      description（策略描述）字段
    """
    data = await list_strategies(db)
    return {"success": True, "data": [d.model_dump() for d in data]}


@router.get("/virtual-accounts")
async def list_virtual_accounts_api():
    """
    获取虚拟账户列表

    业务角度：
      用户在配置栏的"虚拟账户"下拉框中需要选择一个账户来承载回测。
      虚拟账户包含初始资金、手续费率、持仓等配置，回测过程中所有的
      模拟交易都在该账户体系下运行（扣款、建仓、平仓、算手续费等）。

    技术角度：
      GET 方法，无请求参数。返回的列表数据量通常很小（几个账户），
      不需要分页。service 层目前返回 mock 数据，后续对接 account_trading
      模块获取真实虚拟账户配置。

    参数：
      无

    返回值：
      {"success": True, "data": [VirtualAccountInfo, ...]}
      data 为虚拟账户信息列表，每项包含 id（账户ID）、name（账户名称）、
      initial_capital（初始资金）、commission_rate（手续费率）字段
    """
    data = await list_virtual_accounts()
    return {"success": True, "data": [d.model_dump() for d in data]}


# ============================================================
# 回测控制接口
# 管理回测会话的生命周期：创建会话、查询状态、暂停/继续/停止、调速
# ============================================================

@router.post("/start")
async def start_replay_api(req: ReplayStartRequest):
    """
    启动回测

    业务角度：
      用户在配置栏完成所有选项后，点击"开始回测"按钮触发此接口。
      后端根据配置（股票代码、策略、虚拟账户、时间范围、K线周期）
      初始化一个回测会话，准备历史数据和策略引擎，返回会话ID。
      前端拿到会话ID后，轮询 /session/{id} 获取进度，
      并调用 /kline/{id}、/signals/{id} 等接口获取数据渲染图表。

    技术角度：
      POST 方法，因为启动回测是一个有副作用的操作（创建会话、加载资源）。
      req 包含回测所需的全部配置参数，service 层返回包含 session_id
      和 total_bars 的响应，前端据此初始化播放进度条和图表。

    参数：
      req.stock_code (str):   股票代码，如 "000001.SZ"，指定回测标的
      req.strategy_id (int):  策略ID，指定使用哪个策略驱动回测
      req.account_id (int):   虚拟账户ID，指定使用哪个账户承载模拟交易
      req.timeframe (str):    K线周期，如 "1d"/"1h"/"5m"，
                              决定每根K线的时间跨度和策略触发频率
      req.start_date (str):   回测起始日期，格式 "YYYY-MM-DD"
      req.end_date (str):     回测结束日期，格式 "YYYY-MM-DD"

    返回值：
      {"success": True, "data": {"session_id": int, "total_bars": int}}
      session_id 为回测会话唯一标识，后续所有数据接口都需要传此ID；
      total_bars 为该时间范围内的K线总根数，前端据此设置进度条最大值
    """
    data = await start_replay(
        stock_code=req.stock_code,
        strategy_id=req.strategy_id,
        account_id=req.account_id,
        timeframe=req.timeframe,
        start_date=req.start_date,
        end_date=req.end_date,
    )
    return {"success": True, "data": data.model_dump()}


@router.get("/session/{session_id}")
async def get_session_api(session_id: int):
    """
    获取回测会话状态

    业务角度：
      前端在回测运行期间需要实时了解当前进度（已播放到第几根K线）、
      运行状态（运行中/已暂停/已完成/已停止），以便更新播放控制条
      和进度条。此接口供前端轮询调用（或后续升级为 WebSocket 推送）。

    技术角度：
      GET 方法，session_id 作为路径参数。返回当前会话的快照状态，
      包括 current_index（当前播放位置）和 status（运行状态枚举）。
      前端根据 status 决定播放/暂停按钮的显示状态，
      根据 current_index / total_bars 计算进度条百分比。

    参数：
      session_id (int): 路径参数，回测会话ID，由 /start 接口返回

    返回值：
      {"success": True, "data": {"session_id": int, "status": str,
       "current_index": int, "total_bars": int}}
      session_id 为会话ID；status 为运行状态，取值：
        "running"（运行中）、"paused"（已暂停）、
        "completed"（已完成）、"stopped"（已停止）；
      current_index 为当前播放到的K线索引（从0开始）；
      total_bars 为K线总根数
    """
    data = await get_session(session_id)
    return {"success": True, "data": data.model_dump()}


@router.post("/control")
async def control_replay_api(req: ReplayControlRequest):
    """
    控制回测（暂停/继续/停止）

    业务角度：
      用户在回测播放过程中可以手动控制回测的运行状态：
      - 暂停：暂时停止K线推进，方便观察某个时间点的数据和交易信号
      - 继续：从暂停处恢复播放
      - 停止：彻底终止回测，不可恢复
      这三种操作对应前端播放控制条的三个按钮。

    技术角度：
      POST 方法，因为暂停/继续/停止都是改变服务端状态的有副作用操作。
      req.action 为操作类型枚举，service 层根据 action 修改会话状态
      并返回更新后的会话快照。

    参数：
      req.session_id (int): 回测会话ID
      req.action (str):     控制动作，取值：
                            "pause"（暂停）、"resume"（继续）、"stop"（停止）

    返回值：
      {"success": True, "data": {"session_id": int, "status": str,
       "current_index": int, "total_bars": int}}
      返回更新后的会话状态快照，结构与 /session/{id} 一致，
      前端据此更新播放控制条和进度条
    """
    data = await control_replay(req.session_id, req.action)
    return {"success": True, "data": data.model_dump()}


@router.post("/speed")
async def set_replay_speed_api(req: ReplaySpeedRequest):
    """
    设置回测播放速度

    业务角度：
      用户可以通过播放控制条的速度按钮调整K线推进速度（1x/2x/4x/8x），
      快速跳过不感兴趣的区间，或在关键拐点放慢观察。
      1x 表示每秒推进1根K线，2x 表示每秒推进2根，以此类推。

    技术角度：
      POST 方法，因为调速会改变服务端的回测推进频率。
      req.speed 为倍速值，service 层将此值记录到会话配置中，
      回测引擎根据 speed 调整推送频率。

    参数：
      req.session_id (int): 回测会话ID
      req.speed (int):      播放倍速，取值 1/2/4/8，
                            1=每秒1根K线，2=每秒2根，以此类推

    返回值：
      {"success": True, "data": {"session_id": int, "status": str,
       "current_index": int, "total_bars": int}}
      返回更新后的会话状态快照，前端据此确认调速成功
    """
    data = await set_replay_speed(req.session_id, req.speed)
    return {"success": True, "data": data.model_dump()}


# ============================================================
# 回测数据接口
# 获取回测过程中产生的各类数据，用于前端渲染图表和表格
# 所有接口都需要 session_id 路径参数，确保数据归属正确的回测会话
# ============================================================

@router.get("/kline/{session_id}")
async def get_kline_data_api(session_id: int):
    """
    获取回测K线数据

    业务角度：
      K线图是回测页面的核心图表，展示标的价格走势（开高低收）和成交量。
      前端使用 lightweight-charts 渲染，此接口提供其所需的全部数据。
      K线数据在回测启动后一次性加载（历史数据量通常可控），
      前端根据播放进度控制可见范围。

    技术角度：
      GET 方法，session_id 作为路径参数标识回测会话。
      返回 OHLCV 数据列表，每项包含 time（时间戳）、open/high/low/close
      （开高低收价格）、volume（成交量）。service 层根据 session 对应的
      股票代码和时间范围，从 api_data 模块获取历史K线数据
      （目前为 mock 数据）。

    参数：
      session_id (int): 路径参数，回测会话ID

    返回值：
      {"success": True, "data": [KlineData, ...]}
      data 为K线数据列表，按时间升序排列，每项包含：
        time (int):    Unix 时间戳（秒）
        open (float):  开盘价
        high (float):  最高价
        low (float):   最低价
        close (float): 收盘价
        volume (float): 成交量
    """
    data = await get_kline_data(session_id)
    return {"success": True, "data": [d.model_dump() for d in data]}


@router.get("/signals/{session_id}")
async def get_trade_signals_api(session_id: int):
    """
    获取交易信号

    业务角度：
      交易信号是策略引擎在回测过程中产生的买卖决策点，前端在K线图上
      用箭头标记展示（买入红色上箭头、卖出绿色下箭头），让用户直观
      看到策略在何时触发了交易。信号与交易记录一一对应。

    技术角度：
      GET 方法，session_id 标识回测会话。返回信号列表，每项包含
      时间、方向、价格、信号名称。前端将信号转换为 lightweight-charts
      的 markers 格式，叠加在K线图上渲染。

    参数：
      session_id (int): 路径参数，回测会话ID

    返回值：
      {"success": True, "data": [TradeSignal, ...]}
      data 为交易信号列表，按时间升序排列，每项包含：
        time (int):    信号触发时间的 Unix 时间戳（秒）
        side (str):    信号方向，"buy"（买入）/ "sell"（卖出）
        price (float): 信号触发价格
        signal (str):  信号名称，如 "均线金叉"/"RSI超卖反弹" 等，
                       标识策略触发的具体条件
    """
    data = await get_trade_signals(session_id)
    return {"success": True, "data": [d.model_dump() for d in data]}


@router.get("/trades/{session_id}")
async def get_trade_records_api(session_id: int):
    """
    获取交易记录

    业务角度：
      每笔交易信号触发后，会在虚拟账户中执行模拟交易（买入建仓或卖出平仓），
      生成一条交易记录。交易记录表展示回测期间的所有成交明细，
      包括方向、价格、数量、手续费、盈亏等，供用户逐笔审查策略表现。

    技术角度：
      GET 方法，session_id 标识回测会话。返回交易记录列表，每项包含
      完整的成交信息。卖出交易的 pnl 字段为该笔平仓的实际盈亏
      （已扣除手续费），买入交易的 pnl 为 0（未平仓无盈亏）。
      前端根据 side 字段渲染方向标签（买入红/卖出绿），
      根据 pnl 正负渲染盈亏数字颜色。

    参数：
      session_id (int): 路径参数，回测会话ID

    返回值：
      {"success": True, "data": [TradeRecord, ...]}
      data 为交易记录列表，按时间升序排列，每项包含：
        id (int):         交易记录ID
        time (str):       成交时间，格式 "YYYY-MM-DD"
        side (str):       交易方向，"buy"（买入）/ "sell"（卖出）
        stock_code (str): 股票代码
        price (float):    成交价格
        quantity (int):   成交数量（股）
        amount (float):   成交金额 = price × quantity
        commission (float): 手续费 = amount × 手续费率
        pnl (float):      盈亏（仅卖出时有值，= 卖出金额 - 买入金额 - 双边手续费）
        signal (str):     触发信号名称
    """
    data = await get_trade_records(session_id)
    return {"success": True, "data": [d.model_dump() for d in data]}


@router.get("/metrics/{session_id}")
async def get_metrics_api(session_id: int):
    """
    获取回测指标

    业务角度：
      回测指标面板展示策略的核心绩效数据，帮助用户快速判断策略优劣：
      - 总收益率/年化收益：衡量策略盈利能力
      - 最大回撤：衡量策略风险，回撤越大幅亏越严重
      - 夏普比率：风险调整后收益，越高说明单位风险收益越好
      - 胜率/盈亏比：衡量交易胜算和赔率
      这些指标在回测完成后为最终值，回测进行中为当前累计值。

    技术角度：
      GET 方法，session_id 标识回测会话。返回单个指标对象（非列表），
      包含 8 项指标。service 层从交易记录和资金曲线中计算得出
      （目前为 mock 数据，后续实现真实计算逻辑）。

    参数：
      session_id (int): 路径参数，回测会话ID

    返回值：
      {"success": True, "data": ReplayMetrics}
      data 为指标对象，包含：
        total_return (float):    总收益率（%）
        annual_return (float):   年化收益率（%）
        max_drawdown (float):    最大回撤（%，负数）
        sharpe_ratio (float):    夏普比率
        win_rate (float):        胜率（%）
        profit_loss_ratio (float): 盈亏比
        trade_count (int):       交易次数（完整买卖算1次）
        total_pnl (float):       总盈亏金额
    """
    data = await get_metrics(session_id)
    return {"success": True, "data": data.model_dump()}


@router.get("/equity/{session_id}")
async def get_equity_curve_api(session_id: int):
    """
    获取资金曲线

    业务角度：
      资金曲线图展示虚拟账户净值随时间的变化，是评估策略整体表现的
      最直观方式。图表采用双轴：左轴为账户净值（金额），右轴为回撤
      （百分比），用户可以同时看到盈利走势和回撤深度。
      回测期间资金曲线随K线推进逐步延伸，回测完成后展示完整曲线。

    技术角度：
      GET 方法，session_id 标识回测会话。返回资金曲线数据列表，
      每项包含时间戳、净值、回撤百分比。前端使用 ECharts 渲染
      双轴折线图（净值线 + 回撤面积图）。
      数据由 service 层从交易记录逐笔累计计算得出
      （目前为 mock 数据，后续实现真实计算逻辑）。

    参数：
      session_id (int): 路径参数，回测会话ID

    返回值：
      {"success": True, "data": [EquityPoint, ...]}
      data 为资金曲线数据列表，按时间升序排列，每项包含：
        time (int):       Unix 时间戳（秒）
        equity (float):   账户净值（金额）
        drawdown (float): 当前回撤百分比（%，0 表示无回撤，负数表示从峰值回撤）
    """
    data = await get_equity_curve(session_id)
    return {"success": True, "data": [d.model_dump() for d in data]}


# ============================================================
# 报告视图数据接口
# 回测完成后，前端切换到报告视图，展示完整的回测分析报告
# 包含基准对比、每日盈亏、每日持仓、运行日志等维度
# ============================================================

@router.get("/benchmark/{session_id}")
async def get_benchmark_data_api(session_id: int):
    """
    获取基准收益数据

    业务角度：
      评估策略不能只看绝对收益，必须与基准指数（如沪深300）对比。
      此接口返回基准指数在回测同期的累计收益率序列，前端与策略收益
      叠加渲染为对比折线图，直观回答"策略跑赢市场了吗"。

    技术角度：
      GET 方法，session_id 标识回测会话。返回基准收益数据列表，
      每项包含 time（日期）和 return_pct（累计收益率%）。

    参数：
      session_id (int): 路径参数，回测会话ID

    返回值：
      {"success": True, "data": [BenchmarkPoint, ...]}
      data 为基准收益数据列表，按时间升序排列，每项包含：
        time (str):         日期，格式 "YYYY-MM-DD"
        return_pct (float): 累计收益率（%）
    """
    data = await get_benchmark_data(session_id)
    return {"success": True, "data": [d.model_dump() for d in data]}


@router.get("/strategy-return/{session_id}")
async def get_strategy_return_data_api(session_id: int):
    """
    获取策略收益数据

    业务角度：
      与基准收益对应，展示策略自身的累计收益曲线。两者在同一张图中
      对比，差距即为超额收益（Alpha）。策略收益从资金曲线推导：
      return_pct = (equity - initial_capital) / initial_capital * 100。

    技术角度：
      GET 方法，session_id 标识回测会话。返回策略收益数据列表，
      每项包含 time（日期）和 return_pct（累计收益率%）。

    参数：
      session_id (int): 路径参数，回测会话ID

    返回值：
      {"success": True, "data": [StrategyReturnPoint, ...]}
      data 为策略收益数据列表，按时间升序排列，每项包含：
        time (str):         日期，格式 "YYYY-MM-DD"
        return_pct (float): 累计收益率（%）
    """
    data = await get_strategy_return_data(session_id)
    return {"success": True, "data": [d.model_dump() for d in data]}


@router.get("/daily-pnl/{session_id}")
async def get_daily_pnl_api(session_id: int):
    """
    获取每日盈亏数据

    业务角度：
      累计收益曲线掩盖了日度波动细节。每日盈亏柱状图展示策略每天
      赚了还是亏了、金额多大，帮助用户识别收益集中度和波动节奏。
      同时展示每日的买入/卖出金额，理解资金流向。

    技术角度：
      GET 方法，session_id 标识回测会话。返回每日盈亏数据列表，
      每项包含 time（日期）、pnl（当日盈亏金额）、buy_amount
      （当日买入金额）、sell_amount（当日卖出金额）。

    参数：
      session_id (int): 路径参数，回测会话ID

    返回值：
      {"success": True, "data": [DailyPnlPoint, ...]}
      data 为每日盈亏数据列表，按时间升序排列，每项包含：
        time (str):        日期，格式 "YYYY-MM-DD"
        pnl (float):       当日盈亏金额
        buy_amount (float): 当日买入金额
        sell_amount (float): 当日卖出金额
    """
    data = await get_daily_pnl(session_id)
    return {"success": True, "data": [d.model_dump() for d in data]}


@router.get("/daily-positions/{session_id}")
async def get_daily_positions_api(session_id: int):
    """
    获取每日持仓数据

    业务角度：
      交易记录只记录买卖时刻，不反映"两次交易之间"的持仓状态。
      每日持仓表让用户看到每一天持了多少股、值多少钱、当天涨跌多少，
      是理解策略持仓节奏和风险敞口的关键数据。

    技术角度：
      GET 方法，session_id 标识回测会话。返回每日持仓数据列表，
      每项包含 time（日期）、quantity（持仓数量）、market_value
      （持仓市值）、daily_pnl（当日盈亏）、daily_return_pct
      （当日收益率%）、total_equity（账户总资产）。

    参数：
      session_id (int): 路径参数，回测会话ID

    返回值：
      {"success": True, "data": [DailyPositionPoint, ...]}
      data 为每日持仓数据列表，按时间升序排列，每项包含：
        time (str):              日期，格式 "YYYY-MM-DD"
        quantity (int):          持仓数量（股）
        market_value (float):    持仓市值
        daily_pnl (float):       当日盈亏金额
        daily_return_pct (float): 当日收益率（%）
        total_equity (float):    账户总资产
    """
    data = await get_daily_positions(session_id)
    return {"success": True, "data": [d.model_dump() for d in data]}


@router.get("/logs/{session_id}")
async def get_replay_logs_api(session_id: int):
    """
    获取回测日志

    业务角度：
      策略运行过程中除了买卖信号，还会产生各种运行信息：初始化参数、
      信号触发原因、风险警告、异常处理等。日志是回测过程最详细的记录，
      帮助用户排查策略逻辑问题（如"为什么这个信号没有触发"）。

    技术角度：
      GET 方法，session_id 标识回测会话。返回日志列表，
      每项包含 time（日志时间）、level（级别：info/warn/error）、
      message（日志内容）。前端按级别用不同颜色标签渲染。

    参数：
      session_id (int): 路径参数，回测会话ID

    返回值：
      {"success": True, "data": [ReplayLogEntry, ...]}
      data 为日志条目列表，按时间升序排列，每项包含：
        time (str):    日志时间，格式 "YYYY-MM-DD"
        level (str):   日志级别，"info" / "warn" / "error"
        message (str): 日志内容
    """
    data = await get_replay_logs(session_id)
    return {"success": True, "data": [d.model_dump() for d in data]}
class FeeRulePayload(BaseModel):
    commission_rate: Decimal = Decimal("0.00025")
    min_commission: Decimal = Decimal("5")
    stamp_tax_rate_sell: Decimal = Decimal("0.0005")
    transfer_fee_rate: Decimal = Decimal("0")


class SlippageRulePayload(BaseModel):
    type: Literal["none", "fixed_tick", "percent"] = "none"
    value: Decimal = Decimal("0")


class BacktestBarPayload(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    trade_date: date
    timestamp: datetime
    open: Decimal = Field(..., gt=0)
    high: Decimal = Field(..., gt=0)
    low: Decimal = Field(..., gt=0)
    close: Decimal = Field(..., gt=0)
    volume: int = Field(..., ge=0)
    amount: Decimal | None = None
    name: str | None = Field(default=None, max_length=128)
    exchange: str | None = Field(default=None, max_length=16)
    limit_up: Decimal | None = None
    limit_down: Decimal | None = None
    paused: bool = False

    @model_validator(mode="after")
    def validate_ohlc(self) -> "BacktestBarPayload":
        if self.high < self.low:
            raise ValueError("K 线 high 不能小于 low")
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError("K 线 open/close 必须落在 low 与 high 之间")
        return self


class BacktestOrderPayload(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    side: Literal["buy", "sell"]
    quantity: int = Field(..., gt=0)
    signal_time: datetime
    price: Decimal | None = Field(default=None, gt=0)
    order_type: Literal["market", "limit"] = "limit"
    signal_id: str | None = Field(default=None, max_length=64)
    remark: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_limit_price(self) -> "BacktestOrderPayload":
        if self.order_type == "limit" and self.price is None:
            raise ValueError("限价单必须提供 price")
        if self.quantity % 100 != 0:
            raise ValueError("A 股回测委托数量必须是 100 股的整数倍")
        return self


class BacktestRunRequest(BaseModel):
    account_id: int = Field(..., gt=0)
    strategy_id: str = Field(default="manual_backtest", min_length=1, max_length=64)
    version_id: str | None = Field(default=None, max_length=64)
    run_id: str | None = Field(default=None, max_length=64)
    start_date: date
    end_date: date
    initial_cash: Decimal = Field(..., gt=0)
    benchmark_symbol: str | None = Field(default=None, max_length=32)
    frequency: str = Field(default="daily", max_length=16)
    fee_rule: FeeRulePayload = Field(default_factory=FeeRulePayload)
    slippage_rule: SlippageRulePayload = Field(default_factory=SlippageRulePayload)
    volume_limit_ratio: Decimal = Field(default=Decimal("0.1"), gt=0, le=1)
    bars: list[BacktestBarPayload] = Field(..., min_length=1)
    orders: list[BacktestOrderPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dates(self) -> "BacktestRunRequest":
        if self.start_date > self.end_date:
            raise ValueError("回测开始日期不能晚于结束日期")
        for bar in self.bars:
            if bar.trade_date < self.start_date or bar.trade_date > self.end_date:
                raise ValueError("K 线日期必须在回测区间内")
        return self


def ok(data: Any, message: str = "ok") -> ApiResponse:
    return ApiResponse(success=True, data=data, message=message)


def raise_api_error(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail={"message": str(exc), "type": "not_found"}) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail={"message": str(exc), "type": "invalid_request"}) from exc
    raise HTTPException(status_code=500, detail={"message": str(exc), "type": type(exc).__name__}) from exc


@router.post("/backtest/run", response_model=ApiResponse)
async def run_backtest(payload: BacktestRunRequest, db: AsyncSession = Depends(get_db)):
    """运行第一版回测交易闭环：K 线撮合、订单成交、资金持仓落库。"""
    try:
        context = BacktestContext(
            account_id=payload.account_id,
            run_id=payload.run_id or f"BTRUN-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6].upper()}",
            strategy_id=payload.strategy_id,
            version_id=payload.version_id,
            start_date=payload.start_date,
            end_date=payload.end_date,
            initial_cash=payload.initial_cash,
            benchmark_symbol=payload.benchmark_symbol,
            frequency=payload.frequency,
            fee_rule=FeeRule(**payload.fee_rule.model_dump()),
            slippage_rule=SlippageRule(**payload.slippage_rule.model_dump()),
            volume_limit_ratio=payload.volume_limit_ratio,
        )
        bars = [BacktestBar(**item.model_dump()) for item in payload.bars]
        orders = [BacktestOrderRequest(**item.model_dump()) for item in payload.orders]
        data = await BacktestTradingEngine(db).run(context, bars, orders)
        return ok(data, "回测交易闭环已完成")
    except Exception as exc:
        raise_api_error(exc)


@router.post("/start", response_model=ApiResponse)
async def start_replay(payload: BacktestRunRequest, db: AsyncSession = Depends(get_db)):
    """兼容历史回放启动入口，当前直接运行第一版回测。"""
    return await run_backtest(payload, db)


@router.get("/session/{run_id}", response_model=ApiResponse)
async def get_replay_session(run_id: str, db: AsyncSession = Depends(get_db)):
    """获取回测运行批次。"""
    result = await db.execute(select(BacktestRun).where(BacktestRun.run_id == run_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={"message": "回测运行批次不存在", "type": "not_found"})
    return ok(
        {
            "id": row.id,
            "run_id": row.run_id,
            "account_id": row.account_id,
            "strategy_id": row.strategy_id,
            "version_id": row.version_id,
            "start_date": row.start_date.isoformat(),
            "end_date": row.end_date.isoformat(),
            "initial_cash": str(row.initial_cash),
            "benchmark_symbol": row.benchmark_symbol,
            "frequency": row.frequency,
            "params": row.params_json or {},
            "status": row.status,
            "created_at": row.created_at.isoformat(sep=" ", timespec="seconds") if row.created_at else "",
            "updated_at": row.updated_at.isoformat(sep=" ", timespec="seconds") if row.updated_at else "",
        },
        "回测运行批次已获取",
    )


@router.get("/trades/{run_id}", response_model=ApiResponse)
async def get_replay_trades(run_id: str, db: AsyncSession = Depends(get_db)):
    """获取回测成交记录。"""
    result = await db.execute(
        select(BacktestTrade)
        .where(BacktestTrade.run_id == run_id)
        .order_by(BacktestTrade.traded_at.asc(), BacktestTrade.id.asc())
    )
    rows = result.scalars().all()
    return ok(
        [
            {
                "trade_id": row.trade_id,
                "broker_trade_id": row.broker_trade_no,
                "order_id": row.broker_order_no or str(row.order_id or ""),
                "symbol": row.symbol,
                "name": row.name or "",
                "side": row.side,
                "price": str(row.price),
                "quantity": int(row.quantity or 0),
                "amount": str(row.amount),
                "commission": str(row.commission or "0"),
                "stamp_tax": str(row.stamp_tax or "0"),
                "transfer_fee": str(row.transfer_fee or "0"),
                "traded_at": row.traded_at.isoformat(sep=" ", timespec="seconds") if row.traded_at else "",
            }
            for row in rows
        ],
        "回测成交记录已获取",
    )
