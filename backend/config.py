from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Core
    FUBOT_ENV: str = Field(default="development")
    FUBOT_SECRET_KEY: str = Field(min_length=32)
    FUBOT_ENCRYPTION_KEY: str = Field(min_length=32)

    # Database
    DATABASE_URL: str

    # Binance
    BINANCE_API_KEY: str
    BINANCE_API_SECRET: str
    BINANCE_TESTNET: bool = True

    # Admin
    ADMIN_USERNAME: str
    ADMIN_PASSWORD: str

    # VPS / Domain
    VPS_HOST: str = "10.10.30.25"
    DOMAIN: str = "fubot.bedikian.cc"

    # Trading defaults
    RISK_PERCENT: float = Field(default=1.0, ge=0.1, le=10.0)
    MAX_POSITIONS: int = Field(default=3, ge=1, le=10)
    DAILY_LOSS_LIMIT_R: float = Field(default=-3.0)
    RR_MIN: float = Field(default=3.0, ge=2.0, le=5.0)
    CHECK_INTERVAL_SECONDS: int = Field(default=60, ge=10, le=300)
    LEVERAGE_BTC: int = Field(default=10, ge=1, le=125)
    LEVERAGE_ALTS: int = Field(default=5, ge=1, le=125)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()
