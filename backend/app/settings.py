from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT_DIR / ".autotag"
SETTINGS_FILE = STATE_DIR / "settings.json"
DB_FILE = STATE_DIR / "autotag.sqlite3"


class Settings(BaseSettings):
    uit_email: str = Field(default="", alias="UIT_EMAIL")
    uit_password: str = Field(default="", alias="UIT_PASSWORD")
    openai_compat_base_url: str = Field(
        default="https://api.openai.com/v1", alias="OPENAI_COMPAT_BASE_URL"
    )
    openai_compat_api_key: str = Field(default="", alias="OPENAI_COMPAT_API_KEY")
    openai_compat_model: str = Field(default="gpt-4o-mini", alias="OPENAI_COMPAT_MODEL")
    auto_submit_enabled: bool = Field(default=False, alias="AUTO_SUBMIT_ENABLED")

    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

