from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime settings loaded from the RAGELIT environment."""

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_prefix="RAGELIT_",
        extra="ignore",
    )

    environment: Literal["local", "test", "production"] = "local"
    secret_key: SecretStr
    database_admin_url: str = Field(min_length=1)
    database_url: str = Field(min_length=1)
    qdrant_url: AnyHttpUrl
    access_token_minutes: int = Field(default=15, gt=0)
    refresh_session_days: int = Field(default=14, gt=0)
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    data_dir: Path = Path("../data")
    allowed_origins: list[str] = Field(default_factory=list)
    cookie_secure: bool = False

    @model_validator(mode="after")
    def validate_production_security(self) -> Self:
        if self.environment != "production":
            return self

        secret = self.secret_key.get_secret_value()
        if secret == "change-me" or len(secret) < 32:
            raise ValueError("production requires a non-default 32-character secret")
        if not self.cookie_secure:
            raise ValueError("production requires secure cookies")
        return self
