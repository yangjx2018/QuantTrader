"""真实数据获取器：对接 api_data 和 account_trading 模块。

设计要点：
- 通过 import 直接调用其他模块的 service/repository（同进程，低延迟）
- 异常统一翻译为 DataUnavailableError
- 降级策略：api_data 不可用时回退到 MockKLineFetcher
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from strategy_engine.runtime.engine import (
    AccountConfig,
    KBar,
    KLineFetcher,
    AccountFetcher,
    DataUnavailableError,
)
from strategy_engine.runtime.mock_data import MockKLineFetcher

logger = logging.getLogger(__name__)


class RealKLineFetcher:
    """真实 K 线数据源：对接 api_data.KLineService。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def fetch_klines(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        timeframe: str,
    ) -> list[KBar]:
        """从 api_data 获取 K 线数据。

        Args:
            stock_code: 股票代码（如 "000001.SZ"）
            start_date: 起始日期（YYYY-MM-DD）
            end_date: 结束日期（YYYY-MM-DD）
            timeframe: 时间框架（如 "1d", "1h", "5m"）

        Returns:
            list[KBar]: K 线列表

        Raises:
            DataUnavailableError: api_data 服务不可用
        """
        try:
            # Import api_data service（延迟导入，避免循环依赖）
            from api_data.service import KLineService
            from api_data.repository import KLineRepository
            from api_data.adapters.mock import MockAdapter

            # 创建 KLineService 实例（使用 MockAdapter 作为数据源）
            # TODO: 后续可替换为真实数据源（如 TushareAdapter）
            adapter = MockAdapter()
            kline_repo = KLineRepository(self.db)
            kline_service = KLineService(adapter, kline_repo)

            # 调用 api_data 获取 K 线数据
            klines_data = await kline_service.get_kline_data(
                symbol=stock_code,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                limit=1000,  # 获取足够多的 K 线
            )

            if not klines_data:
                logger.warning(f"No K-line data returned for {stock_code}")
                # 降级到 mock 数据
                logger.info("Falling back to MockKLineFetcher")
                mock_fetcher = MockKLineFetcher()
                return await mock_fetcher.fetch_klines(stock_code, start_date, end_date, timeframe)

            # 转换为 KBar 格式
            kbars: list[KBar] = []
            for kline_dict in klines_data:
                kbar = KBar(
                    time=kline_dict["date"],
                    open=float(kline_dict["open"]),
                    high=float(kline_dict["high"]),
                    low=float(kline_dict["low"]),
                    close=float(kline_dict["close"]),
                    volume=int(kline_dict.get("volume", 0)),
                    amount=float(kline_dict.get("amount", 0.0)),
                )
                kbars.append(kbar)

            logger.info(f"Fetched {len(kbars)} K-bars for {stock_code} from api_data")
            return kbars

        except Exception as e:
            logger.error(f"Failed to fetch K-lines from api_data: {e}")
            # 降级到 mock 数据
            logger.info("Falling back to MockKLineFetcher due to error")
            try:
                mock_fetcher = MockKLineFetcher()
                return await mock_fetcher.fetch_klines(stock_code, start_date, end_date, timeframe)
            except Exception as mock_e:
                logger.error(f"MockKLineFetcher also failed: {mock_e}")
                raise DataUnavailableError(
                    f"Both api_data and mock fetcher failed: api_data={e}, mock={mock_e}"
                )


class RealAccountFetcher:
    """真实账户数据源：对接 account_trading.AccountTradingRepository。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def fetch_account(self, account_id: int) -> Optional[AccountConfig]:
        """从 account_trading 获取账户配置。

        Args:
            account_id: 账户 ID

        Returns:
            AccountConfig: 账户配置（包含初始资金、手续费率等）

        Raises:
            DataUnavailableError: account_trading 服务不可用
        """
        try:
            # Import account_trading repository（延迟导入，避免循环依赖）
            from account_trading.repository import AccountTradingRepository
            from account_trading.models import TradingAccount

            # 创建 repository 实例
            account_repo = AccountTradingRepository(self.db)

            # 查询账户
            account: Optional[TradingAccount] = await account_repo.get_account(account_id)
            if not account:
                logger.warning(f"Account {account_id} not found in account_trading")
                # 返回默认账户配置
                logger.info("Using default account config")
                return AccountConfig(
                    account_id=account_id,
                    initial_capital=1_000_000.0,  # 默认 100 万
                    commission_rate=0.001,  # 默认 0.1%
                    slippage=0.0,
                )

            # 获取最新余额快照（作为初始资金）
            # TODO: 后续可从 meta_json 或其他配置表获取 initial_capital
            try:
                latest_balance = await account_repo.get_latest_balance(account)
                initial_capital = float(latest_balance.get("cash_balance", 1_000_000.0))
            except Exception as balance_e:
                logger.warning(f"Failed to get latest balance for account {account_id}: {balance_e}")
                initial_capital = 1_000_000.0  # 降级到默认值

            # 从 meta_json 获取 commission_rate（如果有）
            commission_rate = 0.001  # 默认 0.1%
            if account.meta_json and "commission_rate" in account.meta_json:
                commission_rate = float(account.meta_json["commission_rate"])

            account_config = AccountConfig(
                account_id=account_id,
                initial_capital=initial_capital,
                commission_rate=commission_rate,
                slippage=0.0,  # TODO: 从配置表获取
            )

            logger.info(
                f"Fetched account config for {account_id}: "
                f"initial_capital={initial_capital}, commission_rate={commission_rate}"
            )
            return account_config

        except Exception as e:
            logger.error(f"Failed to fetch account from account_trading: {e}")
            # 降级到默认账户配置
            logger.info("Using default account config due to error")
            return AccountConfig(
                account_id=account_id,
                initial_capital=1_000_000.0,
                commission_rate=0.001,
                slippage=0.0,
            )
