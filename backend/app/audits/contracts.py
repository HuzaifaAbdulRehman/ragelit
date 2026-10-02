from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,80}$")]
Checksum = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AuditModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Boundary(StrEnum):
    RETRIEVAL_RAW = "retrieval_raw"
    RETRIEVAL_ACCEPTED = "retrieval_accepted"
    CONTEXT = "context"
    OUTPUT_CANDIDATE = "output_candidate"
    OUTPUT_DELIVERED = "output_delivered"
    CITATIONS_CANDIDATE = "citations_candidate"
    CITATIONS_DELIVERED = "citations_delivered"


class Terminal(StrEnum):
    ANSWERED = "answered"
    ABSTAINED = "abstained"
    AUTHENTICATION_DENIED = "authentication_denied"
    VALIDATION_DENIED = "validation_denied"
    RETRIEVAL_REJECTED = "retrieval_rejected"
    CITATIONS_REJECTED = "citations_rejected"
    RUNTIME_FAILED = "runtime_failed"
    OBSERVER_FAILED = "observer_failed"


class Reason(StrEnum):
    CONTROL_PASSED = "control_passed"
    FORBIDDEN_EVIDENCE = "forbidden_evidence"
    POSITIVE_EVIDENCE_MISSING = "positive_evidence_missing"
    INCOMPLETE_EVIDENCE = "incomplete_evidence"
    INVALID_OBSERVATION = "invalid_observation"
    INVENTORY_INCOMPLETE = "inventory_incomplete"


class CanaryMatch(AuditModel):
    canary_id: Identifier
    count: int = Field(default=1, ge=1, le=20)
    offsets: tuple[Annotated[int, Field(ge=0)], ...] = Field(default=(), max_length=20)


class BoundaryEvidence(AuditModel):
    boundary: Boundary
    sequence: int = Field(ge=0)
    state: Literal["observed", "not_reached", "unobserved"] = "observed"
    chunk_ids: tuple[UUID, ...] = Field(default=(), max_length=20)
    canary_matches: tuple[CanaryMatch, ...] = Field(default=(), max_length=20)
    duration_ms: float = Field(default=0.0, ge=0)
    truncated: bool = False

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.state != "observed" and (self.chunk_ids or self.canary_matches):
            raise ValueError("non-observed boundary cannot contain evidence")
        if len(set(self.chunk_ids)) != len(self.chunk_ids):
            raise ValueError("duplicate chunk identifiers")
        return self


class AuditCase(AuditModel):
    id: Identifier
    expected_status: int = Field(ge=100, le=599)
    positive: bool = False
    required_chunks: tuple[UUID, ...] = Field(default=(), max_length=20)
    forbidden_chunks: tuple[UUID, ...] = Field(default=(), max_length=500)
    forbidden_canaries: tuple[Identifier, ...] = Field(default=(), max_length=500)
    required_boundaries: tuple[Boundary, ...] = tuple(Boundary)
    citation_challenge: UUID | None = None

    @model_validator(mode="after")
    def validate_expectations(self) -> Self:
        if self.positive and not self.required_chunks:
            raise ValueError("positive control needs expected evidence")
        if set(self.required_chunks) & set(self.forbidden_chunks):
            raise ValueError("conflicting chunk expectations")
        if len(set(self.required_boundaries)) != len(self.required_boundaries):
            raise ValueError("duplicate required boundary")
        if not self.required_boundaries:
            raise ValueError("control needs boundary evidence")
        return self


class AuditObservation(AuditModel):
    case_id: Identifier
    http_status: int = Field(ge=100, le=599)
    terminal: Terminal
    scope_hash: Checksum | None = None
    boundaries: tuple[BoundaryEvidence, ...] = Field(
        default=(), max_length=len(Boundary)
    )

    @model_validator(mode="after")
    def validate_sequence(self) -> Self:
        names = [stage.boundary for stage in self.boundaries]
        sequences = [stage.sequence for stage in self.boundaries]
        if len(set(names)) != len(names):
            raise ValueError("duplicate boundary")
        if len(set(sequences)) != len(sequences) or sequences != sorted(sequences):
            raise ValueError("invalid observation sequence")
        return self


class AuditCaseResult(AuditModel):
    case_id: Identifier
    status: Literal["pass", "fail", "inconclusive"]
    reason: Reason
    coverage_complete: bool
    first_exposure: Boundary | None = None
    observation: AuditObservation


class RunMetadata(AuditModel):
    profile: Literal["safe", "vulnerable", "deny_all"]
    pack_id: Identifier
    generator_id: Identifier
    embedding_id: Identifier
    provider_id: Identifier
    git_revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    git_dirty: bool
    lock_hashes: tuple[Checksum, ...] = Field(min_length=1, max_length=4)
    template_hash: Checksum
    binding_hash: Checksum
    config_hash: Checksum


class AuditTarget(Protocol):
    def execute(self, case: AuditCase) -> AuditObservation: ...
