"""
Akshare 数据源适配器
使用 akshare 获取真实的 A 股市场数据
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional
import logging

import akshare as ak
import pandas as pd

from .base import DataSourceAdapter

logger = logging.getLogger(__name__)


def _normalize_a_share_code(symbol: str) -> str:
    """000001.SZ / sh600000 → 纯 6 位代码，供 akshare 使用。"""
    text = (symbol or "").strip().upper()
    if not text:
        return text
    if "." in text:
        text = text.split(".", 1)[0]
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix) :]
            break
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits or text


def _normalize_ymd(value: Optional[str], *, default: Optional[str] = None) -> str:
    """统一成 YYYYMMDD。"""
    if not value:
        return default or datetime.now().strftime("%Y%m%d")
    text = str(value).strip().replace("-", "").replace("/", "")[:8]
    return text if len(text) == 8 else (default or datetime.now().strftime("%Y%m%d"))


class AkshareAdapter(DataSourceAdapter):
    """Akshare 数据源适配器（真实市场数据）"""

    def __init__(self):
        self._stock_list_cache: Optional[list[dict]] = None
        self._stock_list_cache_time: Optional[datetime] = None
        self._cache_timeout = timedelta(hours=1)

    async def get_stock_base_info(self, symbol: str) -> dict:
        """获取个股基础信息"""
        code = _normalize_a_share_code(symbol)
        try:
            df = await asyncio.to_thread(ak.stock_info_a_code_name)
            row = df[df['code'] == code]
            if row.empty:
                return {
                    "symbol": symbol,
                    "name": symbol,
                    "market": "A",
                    "sector": None,
                    "IPO_date": None,
                    "total_shares": None,
                    "float_shares": None,
                    "status": "unknown",
                }
            stock = row.iloc[0]
            return {
                "symbol": str(stock['code']),
                "name": str(stock['name']),
                "market": "A",
                "sector": None,
                "IPO_date": None,
                "total_shares": None,
                "float_shares": None,
                "status": "active",
            }
        except Exception as e:
            logger.error(f"get_stock_base_info error: {e}")
            return {
                "symbol": symbol,
                "name": symbol,
                "market": "A",
                "sector": None,
                "IPO_date": None,
                "total_shares": None,
                "float_shares": None,
                "status": "error",
            }

    async def list_stocks(self, market: Optional[str] = None) -> list[dict]:
        """获取股票列表"""
        # 使用缓存
        if self._stock_list_cache and self._stock_list_cache_time:
            if datetime.now() - self._stock_list_cache_time < self._cache_timeout:
                stocks = self._stock_list_cache
                if market:
                    stocks = [s for s in stocks if s.get("market") == market]
                return stocks

        try:
            df = ak.stock_info_a_code_name()
            stocks = []
            for _, row in df.iterrows():
                code = str(row['code'])
                # 判断市场：沪市以6开头，深市以0、2、3开头
                if code.startswith('6'):
                    mkt = "SH"  # 沪市
                elif code.startswith('0') or code.startswith('2') or code.startswith('3'):
                    mkt = "SZ"  # 深市
                else:
                    mkt = "A"
                stocks.append({
                    "symbol": code,
                    "name": str(row['name']).strip(),
                    "market": "A",
                    "sector": None,
                })

            # 缓存
            self._stock_list_cache = stocks
            self._stock_list_cache_time = datetime.now()

            if market:
                return [s for s in stocks if s.get("market") == market]
            return stocks

        except Exception as e:
            logger.error(f"list_stocks error: {e}")
            return []

    async def get_kline_data(
        self,
        symbol: str,
        timeframe: str = "1d",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """获取K线数据（带重试机制）。返回按时间升序。"""
        code = _normalize_a_share_code(symbol)
        end_ymd = _normalize_ymd(end_date)
        if start_date:
            start_ymd = _normalize_ymd(start_date)
        else:
            start_ymd = (datetime.now() - timedelta(days=max(int(limit), 1) * 2)).strftime("%Y%m%d")

        period_map = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "1d": "daily",
            "1w": "weekly",
        }
        period = period_map.get(timeframe, "daily")

        max_retries = 3
        for attempt in range(max_retries):
            try:
                df = await asyncio.to_thread(
                    ak.stock_zh_a_hist,
                    symbol=code,
                    period=period,
                    start_date=start_ymd,
                    end_date=end_ymd,
                    adjust="qfq",
                )

                if df is None or df.empty:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1)
                        continue
                    return []

                klines = []
                for _, row in df.iterrows():
                    raw_ts = row["日期"]
                    if hasattr(raw_ts, "strftime"):
                        ts = raw_ts
                    else:
                        ts = str(raw_ts)
                    klines.append({
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "timestamp": ts,
                        "date": ts if isinstance(ts, str) else ts.strftime("%Y-%m-%d"),
                        "open": float(row["开盘"]),
                        "high": float(row["最高"]),
                        "low": float(row["最低"]),
                        "close": float(row["收盘"]),
                        "volume": float(row["成交量"]),
                        "turnover": float(row.get("成交额", 0) or 0),
                        "amount": float(row.get("成交额", 0) or 0),
                    })

                # 升序；取最近 limit 根
                def _ts_key(item: dict):
                    v = item["timestamp"]
                    return v if hasattr(v, "toordinal") else str(v)

                klines.sort(key=_ts_key)
                if limit and len(klines) > limit:
                    klines = klines[-int(limit) :]
                return klines

            except Exception as e:
                logger.warning(f"get_kline_data attempt {attempt + 1} failed for {symbol}/{code}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                else:
                    logger.error(f"get_kline_data failed after {max_retries} attempts")
                    return []

        return []
    async def get_realtime_quote(self, symbol: str) -> dict:
        """获取实时行情（含五档盘口）"""
        try:
            # 使用 stock_zh_a_spot_em 获取实时行情（含五档）
            df = ak.stock_zh_a_spot_em()

            # 查找目标股票
            stock_row = df[df['代码'] == symbol]

            if stock_row.empty:
                # 如果实时数据没有，尝试使用历史数据
                return await self._get_quote_from_history(symbol)

            row = stock_row.iloc[0]

            # 提取五档数据
            quote = {
                "symbol": symbol,
                "name": str(row.get('名称', symbol)),
                "last_price": float(row.get('最新价', 0)),
                "change": float(row.get('涨跌额', 0)),
                "change_pct": float(row.get('涨跌幅', 0)),
                "open": float(row.get('今开', 0)),
                "high": float(row.get('最高', 0)),
                "low": float(row.get('最低', 0)),
                "volume": float(row.get('成交量', 0)),
                "turnover": float(row.get('成交额', 0)),
                "amplitude": float(row.get('振幅', 0)),
                "market_cap": float(row.get('总市值', 0)) if row.get('总市值') else None,
                "float_market_cap": float(row.get('流通市值', 0)) if row.get('流通市值') else None,
                "pe_ratio": float(row.get('市盈率-动态', 0)) if row.get('市盈率-动态') else None,
                "pb_ratio": float(row.get('市净率', 0)) if row.get('市净率') else None,
                # 五档盘口
                "buy1_price": float(row.get('买一价', 0)) if row.get('买一价') else None,
                "buy1_volume": float(row.get('买一量', 0)) if row.get('买一量') else None,
                "buy2_price": float(row.get('买二价', 0)) if row.get('买二价') else None,
                "buy2_volume": float(row.get('买二量', 0)) if row.get('买二量') else None,
                "buy3_price": float(row.get('买三价', 0)) if row.get('买三价') else None,
                "buy3_volume": float(row.get('买三量', 0)) if row.get('买三量') else None,
                "buy4_price": float(row.get('买四价', 0)) if row.get('买四价') else None,
                "buy4_volume": float(row.get('买四量', 0)) if row.get('买四量') else None,
                "buy5_price": float(row.get('买五价', 0)) if row.get('买五价') else None,
                "buy5_volume": float(row.get('买五量', 0)) if row.get('买五量') else None,
                "sell1_price": float(row.get('卖一价', 0)) if row.get('卖一价') else None,
                "sell1_volume": float(row.get('卖一量', 0)) if row.get('卖一量') else None,
                "sell2_price": float(row.get('卖二价', 0)) if row.get('卖二价') else None,
                "sell2_volume": float(row.get('卖二量', 0)) if row.get('卖二量') else None,
                "sell3_price": float(row.get('卖三价', 0)) if row.get('卖三价') else None,
                "sell3_volume": float(row.get('卖三量', 0)) if row.get('卖三量') else None,
                "sell4_price": float(row.get('卖四价', 0)) if row.get('卖四价') else None,
                "sell4_volume": float(row.get('卖四量', 0)) if row.get('卖四量') else None,
                "sell5_price": float(row.get('卖五价', 0)) if row.get('卖五价') else None,
                "sell5_volume": float(row.get('卖五量', 0)) if row.get('卖五量') else None,
                "timestamp": datetime.now(),
            }

            return quote

        except Exception as e:
            logger.error(f"get_realtime_quote error for {symbol}: {e}")
            return await self._create_empty_quote(symbol)

    async def _get_quote_from_history(self, symbol: str) -> dict:
        """从历史数据获取行情（备用方案）"""
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=(datetime.now() - timedelta(days=5)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
                adjust="qfq"
            )

            if df.empty:
                return await self._create_empty_quote(symbol)

            latest = df.iloc[-1]
            prev_close = df.iloc[-2]['收盘'] if len(df) > 1 else latest['收盘']
            change = latest['收盘'] - prev_close
            change_pct = (change / prev_close * 100) if prev_close > 0 else 0

            return {
                "symbol": symbol,
                "name": symbol,
                "last_price": float(latest['收盘']),
                "change": float(change),
                "change_pct": float(change_pct),
                "open": float(latest['开盘']),
                "high": float(latest['最高']),
                "low": float(latest['最低']),
                "volume": float(latest['成交量']),
                "turnover": float(latest.get('成交额', 0)),
                "amplitude": float(latest.get('振幅', 0)),
                "market_cap": None,
                "float_market_cap": None,
                "pe_ratio": None,
                "pb_ratio": None,
                # 五档为空
                "buy1_price": None,
                "buy1_volume": None,
                "buy2_price": None,
                "buy2_volume": None,
                "buy3_price": None,
                "buy3_volume": None,
                "buy4_price": None,
                "buy4_volume": None,
                "buy5_price": None,
                "buy5_volume": None,
                "sell1_price": None,
                "sell1_volume": None,
                "sell2_price": None,
                "sell2_volume": None,
                "sell3_price": None,
                "sell3_volume": None,
                "sell4_price": None,
                "sell4_volume": None,
                "sell5_price": None,
                "sell5_volume": None,
                "timestamp": datetime.now(),
            }
        except Exception as e:
            logger.error(f"_get_quote_from_history error for {symbol}: {e}")
            return await self._create_empty_quote(symbol)

    async def _create_empty_quote(self, symbol: str) -> dict:
        """创建空的行情数据"""
        return {
            "symbol": symbol,
            "name": symbol,
            "last_price": 0.0,
            "change": 0.0,
            "change_pct": 0.0,
            "open": 0.0,
            "high": 0.0,
            "low": 0.0,
            "volume": 0.0,
            "turnover": 0.0,
            "amplitude": 0.0,
            "market_cap": None,
            "float_market_cap": None,
            "pe_ratio": None,
            "pb_ratio": None,
            # 五档为空
            "buy1_price": None,
            "buy1_volume": None,
            "buy2_price": None,
            "buy2_volume": None,
            "buy3_price": None,
            "buy3_volume": None,
            "buy4_price": None,
            "buy4_volume": None,
            "buy5_price": None,
            "buy5_volume": None,
            "sell1_price": None,
            "sell1_volume": None,
            "sell2_price": None,
            "sell2_volume": None,
            "sell3_price": None,
            "sell3_volume": None,
            "sell4_price": None,
            "sell4_volume": None,
            "sell5_price": None,
            "sell5_volume": None,
            "timestamp": datetime.now(),
        }

    async def get_batch_realtime_quote(self, symbols: list[str]) -> list[dict]:
        """批量获取实时行情"""
        quotes = []
        for symbol in symbols:
            quote = await self.get_realtime_quote(symbol)
            quotes.append(quote)
            # 添加小延迟避免请求过快
            await asyncio.sleep(0.1)
        return quotes

    async def list_sectors(self, market: Optional[str] = None) -> list[dict]:
        """获取板块列表"""
        try:
            df = ak.stock_board_industry_name_em()
            sectors = []
            for _, row in df.iterrows():
                sectors.append({
                    "sector_code": str(row.get('板块代码', '')),
                    "sector_name": str(row.get('板块名称', '')),
                    "market": "A",
                    "stock_count": int(row.get('股票数', 0)),
                    "description": None,
                })
            return sectors
        except Exception as e:
            logger.error(f"list_sectors error: {e}")
            return []

    async def get_sector_stocks(self, sector_code: str) -> list[dict]:
        """获取板块成分股"""
        try:
            df = ak.stock_board_industry_cons_em(symbol=sector_code)
            stocks = []
            for _, row in df.iterrows():
                stocks.append({
                    "symbol": str(row.get('代码', '')),
                    "name": str(row.get('名称', '')),
                    "market": "A",
                    "sector": sector_code,
                })
            return stocks
        except Exception as e:
            logger.error(f"get_sector_stocks error for {sector_code}: {e}")
            return []

    async def sync_stock_base_info(self, symbols: Optional[list[str]] = None) -> list[dict]:
        """同步个股基础信息"""
        if symbols:
            stocks = []
            for symbol in symbols:
                info = await self.get_stock_base_info(symbol)
                stocks.append(info)
                await asyncio.sleep(0.1)
            return stocks
        else:
            return await self.list_stocks()

    async def sync_kline_data(
        self,
        symbol: str,
        timeframe: str = "1d",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """同步K线数据"""
        return await self.get_kline_data(symbol, timeframe, start_date, end_date, limit=1000)
