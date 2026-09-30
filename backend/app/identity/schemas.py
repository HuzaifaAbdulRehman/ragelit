from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    organization_slug: str = Field(
        min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$"
    )

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class SwitchOrganizationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_organization_id: UUID


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"]


@dataclass(frozen=True, slots=True)
class LoginResult:
    access_token: str
    refresh_token: str
    session_id: UUID
    refresh_expires_at: datetime


@dataclass(frozen=True, slots=True)
class RefreshResult:
    access_token: str
    refresh_token: str
    session_id: UUID
    refresh_expires_at: datetime
