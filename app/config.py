from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="POWERLAB_",
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("./data")
    database_url: str = "sqlite:///./data/powerlab.db"
    timezone: str = Field(default="Europe/Berlin", validation_alias="TZ")

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"Unknown IANA timezone: {value}") from error
        return value

    sample_rate_hz: int = 100_000
    sleep_checkpoint_s: float = 60.0
    live_preview_hz: int = 250
    acquisition_timeout_s: float = Field(default=3.0, gt=0)
    worker_timeout_s: float = Field(default=30.0, gt=0)

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "measurements").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "exports").mkdir(parents=True, exist_ok=True)


@lru_cache

def get_settings() -> Settings:
    settings = Settings()
    settings.prepare()
    return settings
