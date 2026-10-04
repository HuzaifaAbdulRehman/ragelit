from uuid import uuid4

import pytest

from app.audits.contracts import AuditCase, Boundary, Terminal
from app.audits.fixtures import generate_fixtures
from app.audits.observer import AuditObserver
from app.audits.reports import build_report
from app.audits.scoring import score_case
from app.audits.seeding import login_actor, seed_workspace
from app.audits.target import (
    BundledAuditTarget,
    PreparedPack,
    PreparedRequest,
    prepare_pack,
)
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


@pytest.mark.parametrize(
    "profile,expected_status", [("safe", 200), ("vulnerable", 503)]
)
def test_post_request_failure_keeps_actual_boundary_observations(
    audit_config: AuditConfiguration,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
    expected_status: int,
) -> None:
    template = generate_fixtures()
    with AuditWorkspace(audit_config, template) as workspace:
        seed_workspace(workspace, template)
        logical = next(
            control for control in template.cases if control.id == "org-1:organization"
        )
        document = next(
            document
            for document in template.documents
            if document.id == logical.document_id
        )
        actor = next(actor for actor in template.actors if actor.id == logical.actor_id)
        organization = next(org for org in template.organizations if org.id == "org-1")
        headers = login_actor(
            workspace, email=actor.email, organization_slug=organization.slug
        )
        binding = workspace.bindings.documents[document.id]
        foreign = tuple(
            chunk
            for doc in template.documents
            if doc.organization_id != "org-1"
            for chunk in workspace.bindings.documents[doc.id].chunk_ids
        )
        control = AuditCase(
            id=logical.id,
            expected_status=200,
            positive=True,
            required_chunks=binding.chunk_ids,
            forbidden_chunks=foreign,
        )
        pack = PreparedPack(
            uuid4(),
            (control,),
            {control.id: PreparedRequest(document.question, headers)},
            template.canaries,
            frozenset(
                chunk
                for bound in workspace.bindings.documents.values()
                for chunk in bound.chunk_ids
            ),
        )
        target = BundledAuditTarget(workspace, pack, profile=profile, lab=True)

        def fail_snapshot() -> None:
            raise RuntimeError("SyntheticSensitiveRuntimeMarker")

        monkeypatch.setattr(workspace, "_save_snapshot", fail_snapshot)
        with pytest.raises(RuntimeError, match="SyntheticSensitiveRuntimeMarker"):
            target.execute(control)
        observed = getattr(target, "last_observation", None)
        assert observed is not None
        assert observed.http_status == expected_status
        assert score_case(control, observed).status == (
            "pass" if profile == "safe" else "fail"
        )
        if profile == "vulnerable":
            assert (
                score_case(control, observed).first_exposure == Boundary.RETRIEVAL_RAW
            )
