from uuid import uuid4

import pytest

from app.audits.contracts import Boundary, Terminal
from app.audits.fixtures import generate_fixtures
from app.audits.observer import AuditObserver
from app.audits.reports import build_report
from app.audits.scoring import score_case
from app.audits.seeding import seed_workspace
from app.audits.target import BundledAuditTarget, prepare_pack
from app.audits.workspace import AuditConfiguration, AuditWorkspace, AuditWorkspaceError
from tests.integration.audits.support import audit_config as audit_config
from tests.unit.audits.support import metadata


@pytest.mark.parametrize(
    "profile,expected_code", [("safe", 0), ("vulnerable", 1), ("deny_all", 1)]
)
def test_full_pack_uses_real_api_with_complete_literal_gates(
    audit_config: AuditConfiguration, profile: str, expected_code: int
) -> None:
    template = generate_fixtures()
    with AuditWorkspace(audit_config, template) as workspace:
        seed_workspace(workspace, template)
        base_documents = dict(workspace.bindings.documents)
        pack = prepare_pack(workspace, uuid4())
        assert tuple(case.id for case in pack.cases) == tuple(
            case.id for case in template.cases
        )
        assert len(pack.cases) == 51
        target = BundledAuditTarget(workspace, pack, profile=profile, lab=True)
        results = tuple(score_case(case, target.execute(case)) for case in pack.cases)
        report = build_report(
            pack.cases, results, metadata().model_copy(update={"profile": profile})
        )
        assert report.exit_code == expected_code
        assert report.coverage_complete
        assert len(report.results) == 51
        assert workspace.bindings.documents == base_documents
        assert len(workspace.bindings.instances) == 18
        assert all(
            instance.state == "complete"
            for instance in workspace.bindings.instances.values()
        )
        if profile == "safe":
            assert all(result.status == "pass" for result in results)
            by_id = {result.case_id: result for result in results}
            for index in range(1, 4):
                assert (
                    by_id[f"org-{index}:membership-revoked"].observation.http_status
                    == 401
                )
                assert by_id[f"org-{index}:metadata"].observation.http_status == 422
                assert by_id[f"org-{index}:citation"].observation.http_status == 502
                assert (
                    by_id[f"org-{index}:version-new"].observation.terminal
                    == Terminal.ANSWERED
                )
            second = prepare_pack(workspace, uuid4())
            second_target = BundledAuditTarget(workspace, second)
            second_results = tuple(
                score_case(case, second_target.execute(case)) for case in second.cases
            )
            assert build_report(second.cases, second_results, metadata()).exit_code == 0
            assert workspace.bindings.documents == base_documents
            assert len(workspace.bindings.instances) == 36
        elif profile == "vulnerable":
            exposed = [result for result in results if result.first_exposure]
            assert exposed
            assert all(
                result.first_exposure == Boundary.RETRIEVAL_RAW for result in exposed
            )
            assert all(
                result.observation.terminal == Terminal.RETRIEVAL_REJECTED
                for result in exposed
            )
        else:
            assert all(
                result.status == "fail"
                for case, result in zip(pack.cases, results, strict=True)
                if case.positive
            )
        serialized = report.model_dump_json()
        assert all(canary not in serialized for canary in pack.canaries.values())
        assert "AuditFixturePasswordMarker" not in serialized
        assert "Bearer " not in serialized


@pytest.mark.parametrize("profile", ["vulnerable", "deny_all"])
def test_broken_target_requires_explicit_lab_opt_in(
    audit_config: AuditConfiguration, profile: str
) -> None:
    template = generate_fixtures()
    with AuditWorkspace(audit_config, template) as workspace:
        with pytest.raises(AuditWorkspaceError, match="audit_lab_opt_in_required"):
            BundledAuditTarget(workspace, None, profile=profile)


def test_missing_raw_observation_cannot_pass_the_complete_pack(
    audit_config: AuditConfiguration, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = generate_fixtures()
    with AuditWorkspace(audit_config, template) as workspace:
        seed_workspace(workspace, template)
        pack = prepare_pack(workspace, uuid4())
        target = BundledAuditTarget(workspace, pack)
        monkeypatch.setattr(
            AuditObserver, "retrieval", lambda self, points, duration: None
        )
        results = tuple(score_case(case, target.execute(case)) for case in pack.cases)
        report = build_report(pack.cases, results, metadata())
        assert report.exit_code == 2
        assert not report.coverage_complete
        assert any(
            stage.boundary == Boundary.RETRIEVAL_RAW and stage.state == "unobserved"
            for result in results
            for stage in result.observation.boundaries
        )


def test_target_rejects_changed_case_labels(
    audit_config: AuditConfiguration,
) -> None:
    template = generate_fixtures()
    with AuditWorkspace(audit_config, template) as workspace:
        seed_workspace(workspace, template)
        pack = prepare_pack(workspace, uuid4())
        target = BundledAuditTarget(workspace, pack)
        forged = pack.cases[0].model_copy(update={"forbidden_chunks": ()})
        before = workspace.bindings.checksum
        with pytest.raises(AuditWorkspaceError, match="audit_case_mismatch"):
            target.execute(forged)
        assert workspace.bindings.checksum == before
