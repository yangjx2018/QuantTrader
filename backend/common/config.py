from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """应用配置，从 .env 读取"""

    # MySQL
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "quant_user"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "quant_trading"

    # 服务
    API_PORT: int = 8000
    API_CORS_ORIGINS: str = "http://localhost:5000"

    # 交易所
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""
    OKX_API_KEY: str = ""
    OKX_API_SECRET: str = ""
    OKX_PASSPHRASE: str = ""

    # 安全
    ENCRYPTION_KEY: str = ""
    # 非空时启用 X-API-Key 鉴权（保护执行启动/下单等写接口）
    API_KEY: str = ""

    # 行情数据源：akshare（真实） / mock（模拟）
    MARKET_DATA_SOURCE: str = "akshare"

    # 策略执行实盘下单：默认不等待人工验证码（失败快返回），避免阻塞 worker
    LIVE_WAIT_MANUAL_CAPTCHA: bool = False
    LIVE_MANUAL_CAPTCHA_TIMEOUT: int = 30
    # 实盘下单放入后台任务，不阻塞其它策略 tick
    LIVE_ORDER_ASYNC: bool = True

    model_config = {
        "env_file": str(ROOT_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"mysql+aiomysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
        )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
