"""Runtime settings. Values come from backend/.env (see .env.example) or the environment."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    llm_provider: str = "openai"  # openai | scripted (no-key demo mode using reference trajectories)
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1"

    approval_threshold: float = 5000.0
    max_deviation_pct: float = 40.0
    max_recovery_turns: int = 2
    max_agent_turns: int = 20

    db_path: str = "./data/purchasing.db"

    @property
    def resolved_db_path(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else (BACKEND_DIR / p).resolve()


settings = Settings()
