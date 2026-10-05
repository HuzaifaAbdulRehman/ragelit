from types import MappingProxyType
from uuid import UUID, uuid4

import pytest
from qdrant_client import QdrantClient, models
from sqlalchemy import text

from app.audits.contracts import Boundary, Terminal
from app.audits.fixtures import generate_fixtures
from app.audits.reports import build_report
from app.audits.scoring import score_case
from app.audits.target import BundledAuditTarget, PreparedPack, prepare_pack
from app.audits.workspace import AuditConfiguration, AuditWorkspace, AuditWorkspaceError
from app.documents.chunking import TextChunk
from app.documents.extraction import DocumentError
from app.retrieval.embeddings import Embedding
from app.retrieval.store import IndexContext, QdrantChunkStore
from tests.integration.audits.support import audit_config as audit_config
from tests.integration.audits.test_isolation_contract import (
    assert_common_controls,
    assert_forged_groups_do_not_grant_document_access,
)
from tests.integration.audits.test_isolation_workspace import TenantResources
from tests.integration.audits.test_isolation_workspace import owned as owned
from tests.unit.audits.support import metadata
from tests.unit.retrieval.test_store_search import FixedEmbeddings


def empty_pack() -> PreparedPack:
    return PreparedPack(
        uuid4(), (), MappingProxyType({}), MappingProxyType({}), frozenset()
    )


@pytest.mark.parametrize("malformed_version", [False, True])
def test_post_filter_keeps_raw_candidates_but_accepts_only_scoped_active_versions(
    audit_config: AuditConfiguration,
    malformed_version: bool,
) -> None:
    from app.audits.isolation_lab import LabPostFilterStore

    with AuditWorkspace(audit_config, generate_fixtures()) as workspace:
        vector = Embedding((1.0,) + (0.0,) * 63, (7,), (1.0,))
        with workspace.mutation():
            for number, organization, active in (
                (0, 1, True),
                (1, 2, True),
                (2, 1, False),
                (3, 1, True),
            ):
                context = IndexContext(
                    UUID(int=organization),
                    UUID(int=20 + number),
                    UUID(int=30 + number),
                    UUID(int=50 + number),
                    "synthetic.txt",
                    "organization",
                    (),
                    (),
                )
                workspace.store.stage(
                    context,
                    (
                        TextChunk(
                            UUID(int=40 + number), 0, "synthetic evidence", "line 1"
                        ),
                    ),
                    FixedEmbeddings(vector, dimension=64),
                )
                if active:
                    workspace.store.activate(context)
            if malformed_version:
                points, _ = workspace.store.client.scroll(
                    workspace.store.collection_name, limit=10
                )
                point = next(
                    point
                    for point in points
                    if point.payload is not None
                    and point.payload["chunk_id"] == str(UUID(int=43))
                )
                workspace.store.client.set_payload(
                    workspace.store.collection_name,
                    payload={"document_version_id": [str(UUID(int=30))]},
                    points=[point.id],
                    wait=True,
                )
        store = LabPostFilterStore(workspace, lab=True)
        filters = models.Filter(
            must=[
                models.FieldCondition(
                    key="document_version_id",
                    match=models.MatchValue(value=str(UUID(int=30))),
                )
            ]
        )
        result = store.search_points(UUID(int=1), (UUID(int=30),), vector, filters, 10)
        assert len(result.raw) == 4
        assert {point.payload["chunk_id"] for point in result.raw if point.payload} == {
            str(UUID(int=value)) for value in (40, 41, 42, 43)
        }
        assert [
            point.payload["chunk_id"] for point in result.accepted if point.payload
        ] == [str(UUID(int=40))]
        before = workspace.bindings.checksum
        with pytest.raises(DocumentError) as stopped:
            store.search_points(UUID(int=1), (), vector, filters, 10)
        assert stopped.value.code == "invalid_query"
        assert workspace.bindings.checksum == before
    with pytest.raises(AuditWorkspaceError, match="audit_workspace_not_open"):
        store.search_points(UUID(int=1), (UUID(int=30),), vector, filters, 10)


@pytest.mark.parametrize(
    "unsafe,expected",
    [
        ("no_opt_in", "audit_lab_opt_in_required"),
        ("foreign_client", "audit_lab_target_mismatch"),
        ("foreign_collection", "audit_lab_target_mismatch"),
        ("vulnerable", "audit_store_conflict"),
        ("deny_all", "audit_store_conflict"),
        ("unowned", "audit_marker_drift"),
    ],
)
def test_target_override_cannot_escape_owned_safe_lab(
    audit_config: AuditConfiguration, unsafe: str, expected: str
) -> None:
    foreign = QdrantClient(":memory:")
    try:
        with AuditWorkspace(audit_config, generate_fixtures()) as workspace:
            original = workspace.store
            store = QdrantChunkStore(
                foreign if unsafe == "foreign_client" else original.client,
                "ordinary_collection"
                if unsafe == "foreign_collection"
                else original.collection_name,
                dimension=original.dimension,
            )
            if unsafe == "unowned":
                with workspace.admin_engine.begin() as connection:
                    connection.execute(
                        text("UPDATE audit_workspace_owner SET binding_hash = :hash"),
                        {"hash": "0" * 64},
                    )
            before = workspace.bindings.checksum
            with pytest.raises(AuditWorkspaceError, match=expected):
                BundledAuditTarget(
                    workspace,
                    empty_pack(),
                    lab=unsafe != "no_opt_in",
                    profile=unsafe if unsafe in {"vulnerable", "deny_all"} else "safe",
                    retrieval_store=store,
                )
            assert workspace.bindings.checksum == before
            assert (
                original.client.count(original.collection_name, exact=True).count == 0
            )
            assert foreign.get_collections().collections == []
    finally:
        foreign.close()


@pytest.mark.parametrize(
    "strategy,expected_code,collection_count",
    [
        ("shared_pre_filter", 0, 1),
        ("tenant_collections", 0, 3),
        ("lab_post_filter", 1, 1),
    ],
)
def test_same_51_case_inventory_and_controls_run_on_all_strategies(
    owned: TenantResources,
    strategy: str,
    expected_code: int,
    collection_count: int,
) -> None:
    from app.audits.isolation_lab import LabPostFilterStore

    physical = (
        "tenant_collections"
        if strategy == "tenant_collections"
        else "shared_pre_filter"
    )
    config = AuditConfiguration.model_validate(
        owned.config.model_dump() | {"vector_strategy": physical}
    )
    template = generate_fixtures()
    with AuditWorkspace(config, template) as workspace:
        try:
            pack = prepare_pack(workspace, uuid4())
        finally:
            if strategy == "tenant_collections":
                owned.capture(workspace)
        assert len(pack.cases) == 51
        assert len(workspace.store.collection_names()) == collection_count
        assert tuple(case.id for case in pack.cases) == tuple(
            case.id for case in template.cases
        )
        store = (
            LabPostFilterStore(workspace, lab=True)
            if strategy == "lab_post_filter"
            else workspace.store
        )
        assert_forged_groups_do_not_grant_document_access(workspace, store)
        target = BundledAuditTarget(
            workspace,
            pack,
            lab=strategy == "lab_post_filter",
            retrieval_store=store if strategy == "lab_post_filter" else None,
        )
        results = tuple(score_case(case, target.execute(case)) for case in pack.cases)
        report = build_report(pack.cases, results, metadata())
        assert report.exit_code == expected_code
        assert report.coverage_complete and len(report.results) == 51
        assert len(workspace.bindings.instances) == 18
        assert all(
            instance.state == "complete"
            for instance in workspace.bindings.instances.values()
        )
        assert_common_controls(pack.cases, results)
        exposed = [result for result in results if result.first_exposure is not None]
        if strategy == "lab_post_filter":
            assert exposed
            assert all(
                result.first_exposure == Boundary.RETRIEVAL_RAW for result in exposed
            )
            for case, result in zip(pack.cases, results, strict=True):
                for stage in result.observation.boundaries:
                    if stage.boundary == Boundary.RETRIEVAL_RAW:
                        continue
                    forbidden = set(case.forbidden_chunks)
                    if (
                        stage.boundary == Boundary.CITATIONS_CANDIDATE
                        and case.citation_challenge is not None
                        and result.observation.terminal == Terminal.CITATIONS_REJECTED
                        and result.observation.http_status == 502
                    ):
                        forbidden.discard(case.citation_challenge)
                    assert not set(stage.chunk_ids) & forbidden
                    assert not {
                        match.canary_id for match in stage.canary_matches
                    } & set(case.forbidden_canaries)
        else:
            assert not exposed
            assert all(result.status == "pass" for result in results)
