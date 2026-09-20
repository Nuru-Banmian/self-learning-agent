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
