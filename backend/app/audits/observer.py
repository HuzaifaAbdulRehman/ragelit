import hashlib
import json
import math
from collections.abc import Collection, Mapping, Sequence
from typing import Literal
from uuid import UUID

from qdrant_client import models

from app.audits.contracts import (
    AuditObservation,
    Boundary,
    BoundaryEvidence,
    CanaryMatch,
    Terminal,
)
from app.retrieval.contracts import AuthorizedChunk
from app.tenancy.scope import AccessScope

_GENERATION = {
    Boundary.OUTPUT_CANDIDATE,
    Boundary.OUTPUT_DELIVERED,
    Boundary.CITATIONS_CANDIDATE,
    Boundary.CITATIONS_DELIVERED,
}
_DELIVERY = {Boundary.OUTPUT_DELIVERED, Boundary.CITATIONS_DELIVERED}


class AuditObserver:
    def __init__(
        self,
        *,
        case_id: str,
        canaries: Mapping[str, str],
        known_chunk_ids: Collection[UUID],
    ) -> None:
        if any(not value for value in canaries.values()) or len(
            set(canaries.values())
        ) != len(canaries):
            raise ValueError("invalid canary registry")
        for identifier in canaries:
            CanaryMatch(canary_id=identifier)
        self.case_id = case_id
        self._canaries = dict(canaries)
        self._known_chunk_ids = frozenset(known_chunk_ids)
        self._stages: list[BoundaryEvidence] = []
        self._failed = False
        self._scope_hash: str | None = None
        self._terminal = Terminal.RUNTIME_FAILED
        self._http_status = 503
        self._finished = False

    def scope(self, scope: AccessScope) -> None:
        payload = {
            "user": str(scope.user_id),
            "organization": str(scope.organization_id),
            "membership": str(scope.membership_id),
            "role": scope.role.value,
            "groups": sorted(str(identifier) for identifier in scope.group_ids),
        }
        self._scope_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _matches(self, texts: tuple[str, ...]) -> tuple[tuple[CanaryMatch, ...], bool]:
        matches: list[CanaryMatch] = []
        truncated = False
        for identifier, value in self._canaries.items():
            offsets: list[int] = []
            for text in texts:
                start = 0
                while (offset := text.find(value, start)) != -1:
                    offsets.append(offset)
                    start = offset + len(value)
                    if len(offsets) > 20:
                        truncated = True
                        break
                if len(offsets) > 20:
                    break
            if offsets:
                matches.append(
                    CanaryMatch(
                        canary_id=identifier,
                        count=min(len(offsets), 20),
                        offsets=tuple(offsets[:20]),
                    )
                )
            if len(matches) > 20:
                truncated = True
                break
        return tuple(matches[:20]), truncated

    def _record(
        self,
        boundary: Boundary,
        identifiers: tuple[UUID, ...] = (),
        texts: tuple[str, ...] = (),
        duration_ms: float = 0.0,
        *,
        state: Literal["observed", "not_reached", "unobserved"] = "observed",
        decision: Literal["normal", "query_skipped"] = "normal",
    ) -> None:
        if any(stage.boundary == boundary for stage in self._stages):
            self._failed = True
            return
        if not math.isfinite(duration_ms) or duration_ms < 0:
            self._failed = True
            duration_ms = 0.0
        if any(identifier not in self._known_chunk_ids for identifier in identifiers):
            self._failed = True
        safe_ids = tuple(
            dict.fromkeys(
                identifier
                for identifier in identifiers
                if identifier in self._known_chunk_ids
            )
        )
        matches, truncated = self._matches(texts[:20])
        self._stages.append(
            BoundaryEvidence(
                boundary=boundary,
                sequence=len(self._stages),
                state=state,
                decision=decision,
                chunk_ids=safe_ids[:20],
                canary_matches=matches,
                duration_ms=duration_ms,
                truncated=truncated or len(safe_ids) > 20 or len(texts) > 20,
            )
        )

    def retrieval(
        self, points: Sequence[models.ScoredPoint], duration_ms: float
    ) -> None:
        identifiers: list[UUID] = []
        texts: list[str] = []
        for point in points:
            payload = point.payload
            if payload is None:
                self._failed = True
                continue
            try:
                identifier = UUID(str(payload["chunk_id"]))
            except (KeyError, TypeError, ValueError):
                self._failed = True
                continue
            identifiers.append(identifier)
            text = payload.get("text")
            if not isinstance(text, str):
                self._failed = True
            else:
                texts.append(text)
        self._record(
            Boundary.RETRIEVAL_RAW, tuple(identifiers), tuple(texts), duration_ms
        )

    def retrieval_skipped(self) -> None:
        self._record(
            Boundary.RETRIEVAL_RAW, state="not_reached", decision="query_skipped"
        )

    def chunks(
        self,
        boundary: Literal["retrieval_accepted", "context"],
        chunks: tuple[AuthorizedChunk, ...],
        duration_ms: float,
    ) -> None:
        self._record(
            Boundary(boundary),
            tuple(chunk.id for chunk in chunks),
            tuple(chunk.text for chunk in chunks),
            duration_ms,
        )

    def generation(
        self, answer: str, citation_ids: tuple[UUID, ...], duration_ms: float
    ) -> None:
        self._record(
            Boundary.OUTPUT_CANDIDATE, texts=(answer,), duration_ms=duration_ms
        )
        self._record(Boundary.CITATIONS_CANDIDATE, identifiers=citation_ids)

    def delivery(self, answer: str | None, citation_ids: tuple[UUID, ...]) -> None:
        self._record(
            Boundary.OUTPUT_DELIVERED, texts=(answer,) if answer is not None else ()
        )
        self._record(Boundary.CITATIONS_DELIVERED, identifiers=citation_ids)

    def finish(self, http_status: int, state: str, code: str | None = None) -> None:
        if http_status == 200 and state == "answered":
            terminal = Terminal.ANSWERED
        elif http_status == 200 and state == "abstained":
            terminal = Terminal.ABSTAINED
        elif http_status in {401, 403}:
            terminal = Terminal.AUTHENTICATION_DENIED
        elif http_status == 422:
            terminal = Terminal.VALIDATION_DENIED
        elif http_status == 503 and code == "invalid_retrieval_projection":
            terminal = Terminal.RETRIEVAL_REJECTED
        elif http_status == 502 and code == "invalid_citations":
            terminal = Terminal.CITATIONS_REJECTED
        else:
            terminal = Terminal.RUNTIME_FAILED
        if self._finished and (
            self._http_status != http_status or self._terminal != terminal
        ):
            self._failed = True
        self._http_status, self._terminal = http_status, terminal
        self._finished = True

    def snapshot(self) -> AuditObservation:
        stages = list(self._stages)
        seen = {stage.boundary for stage in stages}
        terminal = Terminal.OBSERVER_FAILED if self._failed else self._terminal
        for boundary in Boundary:
            if boundary in seen:
                continue
            proven = (
                terminal in {Terminal.AUTHENTICATION_DENIED, Terminal.VALIDATION_DENIED}
                or terminal == Terminal.ABSTAINED
                and boundary in _GENERATION
                or terminal == Terminal.CITATIONS_REJECTED
                and boundary in _DELIVERY
                or terminal == Terminal.RETRIEVAL_REJECTED
                and boundary != Boundary.RETRIEVAL_RAW
            )
            stages.append(
                BoundaryEvidence(
                    boundary=boundary,
                    sequence=len(stages),
                    state="not_reached" if proven else "unobserved",
                )
            )
        return AuditObservation(
            case_id=self.case_id,
            http_status=self._http_status,
            terminal=terminal,
            scope_hash=self._scope_hash,
            boundaries=tuple(stages),
        )
