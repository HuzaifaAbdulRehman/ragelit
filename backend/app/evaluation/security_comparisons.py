import hashlib
import json
from typing import Literal, cast
from uuid import UUID

from app.audits.contracts import (
    AuditCase,
    AuditModel,
    AuditObservation,
    Boundary,
    Checksum,
)
from app.audits.injection_reports import expected_injection_cases
from app.audits.injection_scoring import score_injection
from app.audits.isolation_reports import IsolationStrategy
from app.audits.workspace import DocumentBinding, FixtureBindings
from app.evaluation.access_registry import access_registry
from app.evaluation.access_reports import AccessBenchmarkReport
from app.evaluation.access_reports import _checked_report as checked_access
from app.evaluation.metrics import PairedInterval, paired_mean_interval
from app.evaluation.reports import BenchmarkProvenance
from app.evaluation.security_metrics import boundary_exposure_rate
from app.evaluation.security_reports import InjectionBenchmarkReport
from app.evaluation.security_reports import _checked_report as checked_injection

_ERROR = "security_comparison_invalid"
_BOUNDARIES = (
    Boundary.RETRIEVAL_RAW,
    Boundary.RETRIEVAL_ACCEPTED,
    Boundary.CONTEXT,
    Boundary.OUTPUT_CANDIDATE,
    Boundary.OUTPUT_DELIVERED,
)


class ExposureDifference(AuditModel):
    boundary: Boundary
    interval: PairedInterval


class SecurityComparison(AuditModel):
    pack_id: Literal["access-control-v1", "injection-v1"]
    reference_run_id: UUID
    candidate_run_id: UUID
    reference: IsolationStrategy
    candidate: IsolationStrategy
    reference_gate: Literal[0, 1]
    candidate_gate: Literal[0, 1]
    provenance: BenchmarkProvenance
    input_fingerprint: Checksum
    exposures: tuple[ExposureDifference, ...]
    injection_asr: PairedInterval | None = None
    evidence_notice: Literal[
        "Candidate minus reference, paired by logical case. "
        "Intervals describe this authored cohort, not population risk. "
        "Source gates retain failed controls and exposures. "
        "Checksums verify integrity, not execution or loaded-model attestation."
    ] = (
        "Candidate minus reference, paired by logical case. "
        "Intervals describe this authored cohort, not population risk. "
        "Source gates retain failed controls and exposures. "
        "Checksums verify integrity, not execution or loaded-model attestation."
    )


def _documents(documents: dict[str, DocumentBinding]) -> dict[str, tuple[str, int]]:
    return {
        name: (binding.content_hash, len(binding.chunk_ids))
        for name, binding in documents.items()
    }


def _access_inputs(inventory: FixtureBindings) -> dict[str, object]:
    instances = {}
    for key, instance in inventory.instances.items():
        if instance.document is None:
            raise ValueError(_ERROR)
        instances[key] = (
            instance.state,
            instance.document.content_hash,
            len(instance.document.chunk_ids),
            (instance.previous.content_hash, len(instance.previous.chunk_ids))
            if instance.previous is not None
            else None,
        )
    return {
        "documents": _documents(inventory.documents),
        "instances": instances,
        "organizations": sorted(inventory.organizations),
        "actors": sorted(inventory.actors),
        "groups": sorted(inventory.groups),
        "memberships": sorted(inventory.memberships),
    }


def _compatible(
    reference: AccessBenchmarkReport | InjectionBenchmarkReport,
    candidate: AccessBenchmarkReport | InjectionBenchmarkReport,
    left_inputs: dict[str, object],
    right_inputs: dict[str, object],
) -> str:
    if (
        reference.strategy == candidate.strategy
        or not reference.coverage_complete
        or not candidate.coverage_complete
        or reference.exit_code == 2
        or candidate.exit_code == 2
        or reference.provenance != candidate.provenance
        or reference.provenance.git_dirty
        or candidate.provenance.git_dirty
        or reference.pack_id != candidate.pack_id
        or reference.seed != candidate.seed
        or reference.template_hash != candidate.template_hash
        or left_inputs != right_inputs
    ):
        raise ValueError(_ERROR)
    return hashlib.sha256(
        json.dumps(left_inputs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _exposures(
    sides: tuple[
        tuple[tuple[AuditCase, ...], tuple[AuditObservation, ...]],
        tuple[tuple[AuditCase, ...], tuple[AuditObservation, ...]],
    ],
) -> tuple[ExposureDifference, ...]:
    differences = []
    for boundary in _BOUNDARIES:
        samples = []
        for cases, observations in sides:
            by_id = {observation.case_id: observation for observation in observations}
            values = {}
            for case in cases:
                value = boundary_exposure_rate(
                    (case,), (by_id[case.id],), boundary
                ).primary_rate
                if value is None:
                    raise ValueError(_ERROR)
                values[case.id] = value
            samples.append(values)
        differences.append(
            ExposureDifference(
                boundary=boundary,
                interval=paired_mean_interval(samples[0], samples[1]),
            )
        )
    return tuple(differences)


def compare_access_reports(
    reference: AccessBenchmarkReport, candidate: AccessBenchmarkReport
) -> SecurityComparison:
    reference, candidate = checked_access(reference), checked_access(candidate)
    inputs = _access_inputs(reference.inventory)
    fingerprint = _compatible(
        reference, candidate, inputs, _access_inputs(candidate.inventory)
    )
    sides = tuple(
        (
            access_registry(report.inventory, report.cohort_id).cases,
            tuple(record.observation for record in report.records),
        )
        for report in (reference, candidate)
    )
    return SecurityComparison(
        pack_id=reference.pack_id,
        reference_run_id=reference.run_id,
        candidate_run_id=candidate.run_id,
        reference=reference.strategy,
        candidate=candidate.strategy,
        reference_gate=cast(Literal[0, 1], reference.exit_code),
        candidate_gate=cast(Literal[0, 1], candidate.exit_code),
        provenance=reference.provenance,
        input_fingerprint=fingerprint,
        exposures=_exposures((sides[0], sides[1])),
    )


def compare_injection_reports(
    reference: InjectionBenchmarkReport, candidate: InjectionBenchmarkReport
) -> SecurityComparison:
    reference, candidate = checked_injection(reference), checked_injection(candidate)
    inputs: dict[str, object] = {
        "documents": _documents(reference.documents),
        "trials": reference.trials,
        "provider_profile": reference.provider_profile,
    }
    fingerprint = _compatible(
        reference,
        candidate,
        inputs,
        {
            "documents": _documents(candidate.documents),
            "trials": candidate.trials,
            "provider_profile": candidate.provider_profile,
        },
    )
    sides = []
    attacks = []
    for report in (reference, candidate):
        controls = expected_injection_cases(report.documents, trials=report.trials)
        sides.append(
            (tuple(control.access_case for control in controls), report.observations)
        )
        by_id = {
            observation.case_id: observation for observation in report.observations
        }
        values = {}
        for control in controls:
            if control.attack_id is None:
                continue
            result = score_injection(control, by_id[control.access_case.id])
            if not result.evaluated_attack or not result.coverage_complete:
                raise ValueError(_ERROR)
            values[control.access_case.id] = float(
                result.first_instruction_boundary is not None
            )
        attacks.append(values)
    return SecurityComparison(
        pack_id=reference.pack_id,
        reference_run_id=reference.run_id,
        candidate_run_id=candidate.run_id,
        reference=reference.strategy,
        candidate=candidate.strategy,
        reference_gate=cast(Literal[0, 1], reference.exit_code),
        candidate_gate=cast(Literal[0, 1], candidate.exit_code),
        provenance=reference.provenance,
        input_fingerprint=fingerprint,
        exposures=_exposures((sides[0], sides[1])),
        injection_asr=paired_mean_interval(attacks[0], attacks[1]),
    )
