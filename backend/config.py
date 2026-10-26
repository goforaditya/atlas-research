"""Runtime settings loaded from environment variables (Section 13.2)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_synthesis_model: str = ""
    gemini_timeout_sec: float = 20.0
    llm_price_input_per_mtok_usd: float = 0.0
    llm_price_output_per_mtok_usd: float = 0.0

    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    finnhub_api_key: str = ""
    sec_edgar_user_agent: str = ""

    atlas_db_path: Path = REPO_ROOT / "data" / "atlas_state.db"
    atlas_golden_dir: Path = REPO_ROOT / "data" / "golden_runs"
    atlas_rules_path: Path = REPO_ROOT / "data" / "rules" / "default_red_flags.yaml"
    atlas_frontend_dist: Path = REPO_ROOT / "frontend" / "dist"

    max_live_runs_per_hour_per_ip: int = 10
    daily_llm_budget_usd: float = 5.00

    version: str = "1.0.0"

    @property
    def llm_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def synthesis_model(self) -> str:
        return self.gemini_synthesis_model or self.gemini_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
