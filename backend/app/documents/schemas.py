from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    filename: str
    media_type: str
    state: str
    visibility: str
    created_at: datetime


class AccessCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: Literal["organization", "restricted"]
    user_ids: list[UUID] = Field(default_factory=list, max_length=100)
    group_ids: list[UUID] = Field(default_factory=list, max_length=100)


class VersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    state: str
    chunk_count: int
    created_at: datetime


class AccessResponse(BaseModel):
    visibility: str
    user_ids: list[UUID]
    group_ids: list[UUID]
