import re
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_path: Path = Path("data/assistant.sqlite3")
    dashscope_api_key: SecretStr = SecretStr("")
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    iqs_api_key: SecretStr = SecretStr("")
    iqs_engine: str = "Generic"
    iqs_enhanced_summary: bool = False
    qweather_api_key: SecretStr = SecretStr("")
    qweather_api_host: str = ""
    weather_timeout_seconds: float = Field(default=12, gt=0, le=60)
    max_weather_calls: int = Field(default=4, ge=1, le=6)
    weather_retries: int = Field(default=1, ge=0, le=2)
    weather_forecast_days: int = Field(default=7, ge=1, le=10)
    search_timeout_seconds: float = Field(default=12, gt=0, le=60)
    max_search_calls: int = Field(default=4, ge=1, le=6)
    search_retries: int = Field(default=1, ge=0, le=2)
    user_timezone: str = "Asia/Shanghai"
    model_timeout_seconds: float = Field(default=30, gt=0, le=120)
    run_timeout_seconds: float = Field(default=60, gt=0, le=300)
    max_model_calls: int = Field(default=3, ge=1, le=5)
    model_retries: int = Field(default=1, ge=0, le=3)

    @field_validator("user_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("qweather_api_host")
    @classmethod
    def valid_weather_host(cls, value: str) -> str:
        value = value.strip().lower()
        if value and not re.fullmatch(r"[a-z0-9-]+\.qweatherapi\.com", value):
            raise ValueError("和风 API Host 须为账户专属的 *.qweatherapi.com 域名")
        return value
