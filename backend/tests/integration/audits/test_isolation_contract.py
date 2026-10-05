from app.audits.contracts import AuditCase, AuditCaseResult, Boundary, Terminal
from app.audits.workspace import AuditWorkspace
from app.retrieval.service import AuthorizedRetriever
from app.retrieval.store import QdrantChunkStore
from app.tenancy.enums import Role
from app.tenancy.rls import set_request_context
from app.tenancy.scope import AccessScope


def assert_forged_groups_do_not_grant_document_access(
    workspace: AuditWorkspace, store: QdrantChunkStore
) -> None:
    finance = "org-1-finance-member"
    scope = AccessScope(
        workspace.bindings.actors[finance],
        workspace.bindings.organizations["org-1"],
        workspace.bindings.memberships[finance],
        Role.MEMBER,
        (workspace.bindings.groups["org-1-engineering"],),
    )
    document = next(
        item for item in workspace.template.documents if item.id == "org-1-group"
    )
    expected_denied = workspace.bindings.documents[document.id].chunk_ids
    with workspace.factory() as session:
        set_request_context(
            session, user_id=scope.user_id, organization_id=scope.organization_id
        )
        chunks = AuthorizedRetriever(session, store, workspace.embeddings).search(
            scope, document.question, 20
        )
    assert not set(expected_denied) & {chunk.id for chunk in chunks}


def assert_common_controls(
    cases: tuple[AuditCase, ...], results: tuple[AuditCaseResult, ...]
) -> None:
    by_id = {result.case_id: result for result in results}
    cases_by_id = {case.id: case for case in cases}
    for organization in ("org-1", "org-2", "org-3"):
        for control in ("organization", "user-allowed", "group-allowed", "version-new"):
            result = by_id[f"{organization}:{control}"]
            assert result.observation.http_status == 200
            assert result.observation.terminal == Terminal.ANSWERED
            delivered = next(
                stage
                for stage in result.observation.boundaries
                if stage.boundary == Boundary.CITATIONS_DELIVERED
            )
            assert delivered.state == "observed" and delivered.chunk_ids
            assert set(cases_by_id[result.case_id].required_chunks).issubset(
                delivered.chunk_ids
            )
        assert (
            by_id[f"{organization}:membership-revoked"].observation.http_status == 401
        )
        for control in (
            "user-denied",
            "group-denied",
            "tenant-denied",
            "grant-revoked",
            "group-revoked",
            "deleted",
            "version-old",
        ):
            result = by_id[f"{organization}:{control}"]
            assert result.observation.http_status == 200
            assert result.observation.terminal in {
                Terminal.ANSWERED,
                Terminal.ABSTAINED,
            }
            case = cases_by_id[result.case_id]
            if result.first_exposure is not None:
                assert result.first_exposure == Boundary.RETRIEVAL_RAW
            for stage in result.observation.boundaries:
                if stage.boundary in {Boundary.CONTEXT, Boundary.OUTPUT_DELIVERED}:
                    assert stage.state in {"observed", "not_reached"}
                    assert not set(stage.chunk_ids) & set(case.forbidden_chunks)
                    assert not {
                        match.canary_id for match in stage.canary_matches
                    } & set(case.forbidden_canaries)
