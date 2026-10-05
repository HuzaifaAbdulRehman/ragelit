import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self
from uuid import UUID, uuid4, uuid5

from pydantic import Field, model_validator

from app.audits.artifacts import _checked_path, _read
from app.audits.contracts import (
    AuditCase,
    AuditModel,
    Checksum,
    Identifier,
    RunMetadata,
)
from app.audits.embeddings import FixtureEmbeddings
from app.audits.fixtures import DocumentKind
from app.audits.injection_fixtures import fact_marker, generate_injection_fixtures
from app.audits.injection_providers import LocalInjectionConfiguration
from app.audits.injection_scoring import (
    InjectionCase,
    InjectionResult,
    InjectionSummary,
    score_injection,
    summarize_injection,
)
from app.audits.reports import _publish
from app.audits.workspace import DocumentBinding
from app.chat.provider import SYSTEM


class InjectionProviderRecord(AuditModel):
    mode: Literal["resistant", "obeying", "deny_all", "local"]
    local: LocalInjectionConfiguration | None = None
    prompt_hash: Checksum = hashlib.sha256(SYSTEM.encode()).hexdigest()
    temperature: Literal[0] = 0
    max_tokens: Literal[1024] = 1024
    response_format: Literal["json_object"] = "json_object"

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        if (self.mode == "local") != (self.local is not None):
            raise ValueError("invalid injection provider record")
        return self

    @property
    def identifier(self) -> str:
        return (
            "local-compatible-v1"
            if self.mode == "local"
            else (f"fixture-injection-{self.mode}-v1")
        )


class InjectionReport(AuditModel):
    schema_version: Literal["injection-1"] = "injection-1"
    run_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: RunMetadata
    provider: InjectionProviderRecord
    trials: int = Field(ge=1, le=20)
    documents: dict[Identifier, DocumentBinding]
    documents_hash: Checksum
    required_case_ids: tuple[Identifier, ...] = Field(max_length=120)
    results: tuple[InjectionResult, ...] = Field(max_length=120)
    summary: InjectionSummary
    runtime_failed: bool = False
    scope_notice: Literal[
        "Owned synthetic documents; exact marker matches only. "
        "Deterministic profiles test harness behavior, not model resistance."
    ] = (
        "Owned synthetic documents; exact marker matches only. "
        "Deterministic profiles test harness behavior, not model resistance."
    )
    retrieval_notice: Literal[
        "Retrieval evidence covers returned fused results, not internal "
        "dense or sparse prefetch candidates."
    ] = (
        "Retrieval evidence covers returned fused results, not internal "
        "dense or sparse prefetch candidates."
    )


def _documents_hash(documents: dict[str, DocumentBinding]) -> str:
    payload = {
        name: binding.model_dump(mode="json") for name, binding in documents.items()
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def expected_injection_cases(
    documents: dict[str, DocumentBinding], *, trials: int
) -> tuple[InjectionCase, ...]:
    template = generate_injection_fixtures(trials=trials)
    if set(documents) != {doc.id for doc in template.documents}:
        raise ValueError("injection_artifact_invalid")
    if (
        len({binding.document_id for binding in documents.values()}) != 6
        or len({binding.version_id for binding in documents.values()}) != 6
    ):
        raise ValueError("injection_artifact_invalid")
    for doc in template.documents:
        binding = documents[doc.id]
        if binding.content_hash != hashlib.sha256(
            doc.text.encode()
        ).hexdigest() or binding.chunk_ids != (uuid5(binding.version_id, "0"),):
            raise ValueError("injection_artifact_invalid")
    actors = {actor.id: actor for actor in template.actors}
    docs = {doc.id: doc for doc in template.documents}
    controls = []
    for logical in template.cases:
        actor = actors[logical.actor_id]
        forbidden = tuple(
            doc
            for doc in template.documents
            if doc.organization_id != actor.organization_id
            or not (
                doc.visibility == "organization"
                or actor.id in doc.user_ids
                or actor.group_id in doc.group_ids
            )
        )
        control = AuditCase(
            id=logical.id,
            expected_status=200,
            positive=True,
            required_chunks=documents[logical.document_id].chunk_ids,
            forbidden_chunks=tuple(
                chunk for doc in forbidden for chunk in documents[doc.id].chunk_ids
            ),
            forbidden_canaries=tuple(
                sorted(
                    {
                        identifier
                        for doc in forbidden
                        for identifier in (
                            doc.canary_id,
                            fact_marker(template.seed, doc.id)[0],
                        )
                    }
                )
            ),
        )
        controls.append(
            InjectionCase(
                access_case=control,
                fact_id=fact_marker(template.seed, logical.document_id)[0],
                attack_id=docs[logical.document_id].canary_id
                if docs[logical.document_id].kind == DocumentKind.POISONED
                else None,
            )
        )
    return tuple(controls)


def build_injection_report(
    results: tuple[InjectionResult, ...],
    metadata: RunMetadata,
    *,
    documents: dict[str, DocumentBinding],
    provider: InjectionProviderRecord,
    trials: int,
    runtime_failed: bool = False,
) -> InjectionReport:
    cases = expected_injection_cases(documents, trials=trials)
    expected = {case.access_case.id: case for case in cases}
    checked = tuple(
        score_injection(expected[result.case_id], result.access_control.observation)
        if result.case_id in expected
        else result
        for result in results
    )
    return InjectionReport(
        metadata=metadata,
        provider=provider,
        trials=trials,
        documents=documents,
        documents_hash=_documents_hash(documents),
        required_case_ids=tuple(case.access_case.id for case in cases),
        results=checked,
        summary=summarize_injection(cases, checked, runtime_failed=runtime_failed),
        runtime_failed=runtime_failed,
    )


def _checked_report(report: InjectionReport) -> InjectionReport:
    template = generate_injection_fixtures(trials=report.trials)
    cases = expected_injection_cases(report.documents, trials=report.trials)
    expected = {case.access_case.id: case for case in cases}
    identifiers = tuple(result.case_id for result in report.results)
    if (
        report.metadata.profile != "safe"
        or report.metadata.pack_id != template.pack_id
        or report.metadata.generator_id != template.generator_id
        or report.metadata.embedding_id != FixtureEmbeddings.identifier
        or report.metadata.provider_id != report.provider.identifier
        or report.metadata.template_hash != template.checksum
        or report.provider.prompt_hash != hashlib.sha256(SYSTEM.encode()).hexdigest()
        or report.documents_hash != _documents_hash(report.documents)
        or report.required_case_ids != tuple(expected)
        or len(set(identifiers)) != len(identifiers)
        or not set(identifiers).issubset(expected)
        or report.summary
        != summarize_injection(
            cases, report.results, runtime_failed=report.runtime_failed
        )
    ):
        raise ValueError("injection_artifact_invalid")
    known_chunks = {
        chunk for binding in report.documents.values() for chunk in binding.chunk_ids
    }
    known_canaries = set(template.canaries) | {
        fact_marker(template.seed, doc.id)[0] for doc in template.documents
    }
    for result in report.results:
        if result != score_injection(
            expected[result.case_id], result.access_control.observation
        ):
            raise ValueError("injection_artifact_invalid")
        for stage in result.access_control.observation.boundaries:
            if not set(stage.chunk_ids).issubset(known_chunks) or any(
                match.canary_id not in known_canaries for match in stage.canary_matches
            ):
                raise ValueError("injection_artifact_invalid")
    return report


def _content(report: InjectionReport) -> bytes:
    _checked_report(report)
    content = (report.model_dump_json(indent=2) + "\n").encode()
    if len(content) > 8 * 1024 * 1024 or any(
        marker in content for marker in (b"FACTANSWER", b"AUDITCANARY", b"Bearer ")
    ):
        raise ValueError("injection_artifact_invalid")
    return content


def write_injection_report(report: InjectionReport, directory: Path) -> Path:
    content = _content(report)
    directory = directory.absolute()
    if any(
        part.is_symlink() or part.is_junction()
        for part in (directory, *directory.parents)
    ):
        raise ValueError("injection_artifact_invalid")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{report.run_id}.json"
    receipt = destination.with_suffix(".sha256.json")
    if any(path.exists() or path.is_symlink() for path in (destination, receipt)):
        raise FileExistsError("injection artifact already exists")
    receipt_content = (
        json.dumps(
            {
                "filename": destination.name,
                "sha256": hashlib.sha256(content).hexdigest(),
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    _publish(receipt, receipt_content)
    _publish(destination, content)
    return destination


def validate_injection_report(path: Path) -> InjectionReport:
    path = _checked_path(path)
    run_id = UUID(path.stem)
    if path.name != f"{run_id}.json":
        raise ValueError("injection_artifact_invalid")
    content = _read(path, 8 * 1024 * 1024)
    receipt = json.loads(_read(path.with_suffix(".sha256.json"), 4096))
    if receipt != {
        "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
    }:
        raise ValueError("injection_artifact_invalid")
    report = InjectionReport.model_validate_json(content)
    if report.run_id != run_id:
        raise ValueError("injection_artifact_invalid")
    _content(report)
    return report


def validate_injection_release_directory(
    directory: Path,
) -> tuple[InjectionReport, ...]:
    directory = _checked_path(directory)
    entries = tuple(directory.iterdir())
    if len(entries) != 6:
        raise ValueError("injection_artifact_invalid")
    reports = tuple(
        validate_injection_report(path)
        for path in entries
        if path.suffix == ".json" and not path.name.endswith(".sha256.json")
    )
    expected_files = {
        f"{report.run_id}{suffix}"
        for report in reports
        for suffix in (".json", ".sha256.json")
    }
    profiles = {report.provider.mode: report for report in reports}
    if (
        {entry.name for entry in entries} != expected_files
        or len(reports) != 3
        or set(profiles) != {"resistant", "obeying", "deny_all"}
        or len({report.trials for report in reports}) != 1
        or len(
            {
                (
                    report.metadata.git_revision,
                    report.metadata.git_dirty,
                    report.metadata.lock_hashes,
                )
                for report in reports
            }
        )
        != 1
    ):
        raise ValueError("injection_artifact_invalid")
    for mode, report in profiles.items():
        summary = report.summary
        attacks = 3 * report.trials
        denied = mode == "deny_all"
        obeyed = mode == "obeying"
        if (
            not summary.coverage_complete
            or summary.exit_code != (0 if mode == "resistant" else 1)
            or len(report.results) != 6 * report.trials
            or summary.attempted_attacks != attacks
            or summary.evaluated_attacks != (0 if denied else attacks)
            or summary.evaluated_successes != (attacks if obeyed else 0)
            or summary.observed_signals != (attacks if obeyed else 0)
            or summary.baseline_failures != (attacks if denied else 0)
            or summary.benign_controls_passed != (not denied)
        ):
            raise ValueError("injection_artifact_invalid")
    return reports
