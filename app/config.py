from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="POWERLAB_",
        case_sensitive=False,
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("./data")
    database_url: str = "sqlite:///./data/powerlab.db"

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
