from typing import Literal
from uuid import uuid4

import pytest
from fastapi import FastAPI

from app.audits.contracts import Boundary
from app.audits.injection_fixtures import (
    generate_injection_fixtures,
    injection_cases,
    prepare_injection_pack,
)
from app.audits.injection_providers import InjectionProvider
from app.audits.injection_scoring import score_injection, summarize_injection
from app.audits.target import BundledAuditTarget
from app.audits.workspace import AuditConfiguration, AuditWorkspace, AuditWorkspaceError
from tests.integration.audits.support import audit_config as audit_config


@pytest.mark.parametrize(
    "profile,exit_code,successes,baseline_failures",
    [
        ("resistant", 0, 0, 0),
        ("obeying", 1, 3, 0),
        ("deny_all", 1, 0, 3),
    ],
)
def test_injection_profiles_use_real_owned_chat_boundaries(
    audit_config: AuditConfiguration,
    profile: Literal["resistant", "obeying", "deny_all"],
    exit_code: int,
    successes: int,
    baseline_failures: int,
) -> None:
    template = generate_injection_fixtures()
    with AuditWorkspace(audit_config, template) as workspace:
        pack = prepare_injection_pack(workspace, uuid4())
        controls = injection_cases(pack, template)
        app = workspace.client.app
        assert isinstance(app, FastAPI)
        previous_provider = app.state.generation_provider
        target = BundledAuditTarget(
            workspace, pack, generation_provider=InjectionProvider(profile)
        )
        results = tuple(
            score_injection(control, target.execute(control.access_case))
            for control in controls
        )
        summary = summarize_injection(controls, results)
        assert summary.exit_code == exit_code
        assert summary.coverage_complete
        assert summary.attempted_attacks == 3
        assert summary.evaluated_successes == successes
        assert summary.baseline_failures == baseline_failures
        assert summary.evaluated_attacks == (0 if profile == "deny_all" else 3)
        assert summary.attack_success_rate == (
            None if profile == "deny_all" else successes / 3
        )
        assert app.state.generation_provider is previous_provider
        assert all(result.access_control.first_exposure is None for result in results)
        if profile == "obeying":
            assert summary.observed_signals == 3
            assert all(
                result.first_instruction_boundary == Boundary.OUTPUT_CANDIDATE
                for control, result in zip(controls, results, strict=True)
                if control.attack_id is not None
            )
        for control, result in zip(controls, results, strict=True):
            if control.attack_id is None:
                context = next(
                    stage
                    for stage in result.access_control.observation.boundaries
                    if stage.boundary == Boundary.CONTEXT
                )
                assert all(
                    not match.canary_id.endswith("-canary")
                    for match in context.canary_matches
                )
        serialized = "\n".join(result.model_dump_json() for result in results)
        assert all(value not in serialized for value in pack.canaries.values())
        assert "Bearer " not in serialized
        assert "AuditFixturePasswordMarker" not in serialized
        with pytest.raises(AuditWorkspaceError, match="audit_provider_conflict"):
            BundledAuditTarget(
                workspace,
                pack,
                profile="deny_all",
                lab=True,
                generation_provider=InjectionProvider("resistant"),
            )


def test_fact_markers_from_other_tenants_are_forbidden(
    audit_config: AuditConfiguration,
) -> None:
    template = generate_injection_fixtures(trials=2)
    with AuditWorkspace(audit_config, template) as workspace:
        pack = prepare_injection_pack(workspace, uuid4())
        assert len(pack.cases) == 12
        controls = injection_cases(pack, template)
        org1 = next(
            control
            for control in controls
            if control.access_case.id == "org-1:attack:trial-1"
        )
        assert "org-2-poisoned-fact" in org1.access_case.forbidden_canaries
        assert "org-2-organization-fact" in org1.access_case.forbidden_canaries
        assert "org-1-poisoned-fact" not in org1.access_case.forbidden_canaries
        assert len(pack.canaries) == 9
        wrong_template = generate_injection_fixtures(trials=1)
        with pytest.raises(AuditWorkspaceError, match="injection_inventory_mismatch"):
            injection_cases(pack, wrong_template)
