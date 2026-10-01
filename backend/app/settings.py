import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values


ROOT_DIR = Path(__file__).resolve().parents[2]
# Deployed releases live in throwaway directories behind a symlink, so state and .env can live elsewhere.
STATE_DIR = Path(os.environ.get("AUTOTAG_STATE_DIR") or ROOT_DIR / ".autotag")
ENV_FILE = Path(os.environ.get("AUTOTAG_ENV_FILE") or ROOT_DIR / ".env")
DB_FILE = STATE_DIR / "autotag.sqlite3"

TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    rcr_base_url: str = "http://54.179.122.133:8090"
    rcr_username: str = ""
    rcr_password: str = ""
    openai_compat_base_url: str = "https://api.openai.com/v1"
    openai_compat_api_key: str = ""
    openai_compat_model: str = "ag/gemini-3.8-flash-high"
    openai_compat_review_model: str = "cx/gpt-6-sol"
    openai_compat_review_effort: str = "high"
    auto_submit_enabled: bool = False
    ai_double_check_enabled: bool = True
    image_cache_max: int = 400


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Read settings from the process environment, falling back to the .env file."""
    values = env if env is not None else {**dotenv_values(ENV_FILE), **os.environ}

    def text(name: str, default: str) -> str:
        return str(values.get(name) or default)

    def flag(name: str, default: bool) -> bool:
        raw = values.get(name)
        return default if raw is None or raw == "" else str(raw).strip().lower() in TRUE_VALUES

    def count(name: str, default: int) -> int:
        try:
            return max(0, int(values.get(name) or default))
        except ValueError:
            return default

    defaults = Settings()
    return Settings(
        rcr_base_url=text("RCR_BASE_URL", defaults.rcr_base_url),
        rcr_username=text("RCR_USERNAME", ""),
        rcr_password=text("RCR_PASSWORD", ""),
        openai_compat_base_url=text("OPENAI_COMPAT_BASE_URL", defaults.openai_compat_base_url),
        openai_compat_api_key=text("OPENAI_COMPAT_API_KEY", ""),
        openai_compat_model=text("OPENAI_COMPAT_MODEL", defaults.openai_compat_model),
        openai_compat_review_model=text("OPENAI_COMPAT_REVIEW_MODEL", defaults.openai_compat_review_model),
        openai_compat_review_effort=text("OPENAI_COMPAT_REVIEW_EFFORT", defaults.openai_compat_review_effort),
        auto_submit_enabled=flag("AUTO_SUBMIT_ENABLED", defaults.auto_submit_enabled),
        ai_double_check_enabled=flag("AI_DOUBLE_CHECK_ENABLED", defaults.ai_double_check_enabled),
        image_cache_max=count("IMAGE_CACHE_MAX", defaults.image_cache_max),
    )


# Switches flipped from the UI. They live in memory only, so a restart returns to the .env values.
RUNTIME_OVERRIDES: dict[str, bool] = {}


@lru_cache
def get_settings() -> Settings:
    return load_settings()
