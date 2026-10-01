from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class QueryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=10, ge=1, le=20)


class Citation(BaseModel):
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    filename: str
    location: str


class AnswerResponse(BaseModel):
    query_run_id: UUID
    status: Literal["answered", "abstained", "failed"]
    answer: str | None
    citations: list[Citation]


class StageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    stage: str
    decision: str
    duration_ms: float
    chunk_ids: list[UUID]


class TraceResponse(BaseModel):
    id: UUID
    state: str
    error_code: str | None
    created_at: datetime
    stages: list[StageResponse]
