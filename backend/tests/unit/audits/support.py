from uuid import UUID

from app.audits.contracts import (
    AuditCase,
    AuditObservation,
    Boundary,
    BoundaryEvidence,
    RunMetadata,
    Terminal,
)

ALLOWED = UUID(int=1)
FORBIDDEN = UUID(int=2)


def case(*, positive: bool = True) -> AuditCase:
    return AuditCase(
        id="ORG-001",
        expected_status=200,
        positive=positive,
        required_chunks=(ALLOWED,) if positive else (),
        forbidden_chunks=(FORBIDDEN,),
        forbidden_canaries=("private-fixture",),
        required_boundaries=tuple(Boundary),
    )


def observation() -> AuditObservation:
    return AuditObservation(
        case_id="ORG-001",
        http_status=200,
        terminal=Terminal.ANSWERED,
        scope_hash="1" * 64,
        boundaries=tuple(
            BoundaryEvidence(
                boundary=boundary,
                sequence=index,
                chunk_ids=(ALLOWED,)
                if boundary
                in {
                    Boundary.RETRIEVAL_RAW,
                    Boundary.RETRIEVAL_ACCEPTED,
                    Boundary.CONTEXT,
                    Boundary.CITATIONS_CANDIDATE,
                    Boundary.CITATIONS_DELIVERED,
                }
                else (),
            )
            for index, boundary in enumerate(Boundary)
        ),
    )


def metadata() -> RunMetadata:
    return RunMetadata(
        profile="safe",
        pack_id="access-v1",
        generator_id="synthetic-v1",
        embedding_id="fixture-v1",
        provider_id="fake-v1",
        git_revision="a" * 40,
        git_dirty=False,
        lock_hashes=("b" * 64, "c" * 64),
        template_hash="d" * 64,
        binding_hash="e" * 64,
        config_hash="f" * 64,
    )
