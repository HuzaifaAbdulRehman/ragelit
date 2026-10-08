import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from qdrant_client import QdrantClient, models

from app.audits.embeddings import FixtureEmbeddings
from app.audits.fixtures import DocumentKind, generate_fixtures
from app.audits.injection_fixtures import generate_injection_fixtures
from app.audits.target import prepare_pack
from app.audits.workspace import AuditWorkspace, AuditWorkspaceError
from app.evaluation.dataset import generate_utility_corpus
from app.evaluation.workspace import utility_template
from tests.unit.audits.test_workspace import configuration
from tests.unit.evaluation.workspace_support import MissEmbeddings

pytestmark = pytest.mark.filterwarnings(
    "ignore:Payload indexes have no effect:UserWarning"
)


def test_default_configuration_retains_the_original_workspace_identity(
    tmp_path: Path,
) -> None:
    config = configuration(tmp_path)
    original = {
        "environment": "test",
        "database": "ragelit_audit_unit",
        "database_host": "127.0.0.1",
        "database_port": 5432,
        "qdrant_host": "127.0.0.1",
        "qdrant_port": 6333,
        "qdrant_timeout_seconds": 30,
        "collection": "ragelit_audit_unit",
        "root": str((tmp_path / "ragelit_audit_unit").resolve()),
    }
    digest = hashlib.sha256(
        json.dumps(original, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert config.config_hash == digest
    assert isinstance(
        AuditWorkspace(config, generate_fixtures()).embeddings, FixtureEmbeddings
    )


def test_custom_configuration_binds_embedding_fingerprint_and_geometry(
    tmp_path: Path,
) -> None:
    default = configuration(tmp_path)
    first = configuration(
        tmp_path, embedding_fingerprint="1" * 64, embedding_dimension=384
    )
    changed = configuration(
        tmp_path, embedding_fingerprint="2" * 64, embedding_dimension=384
    )
    resized = configuration(
        tmp_path, embedding_fingerprint="1" * 64, embedding_dimension=128
    )
    assert len({item.config_hash for item in (default, first, changed, resized)}) == 4
    assert len({item.workspace_id for item in (default, first, changed, resized)}) == 4
    provider = MissEmbeddings()
    workspace = AuditWorkspace(first, generate_fixtures(), embeddings=provider)
    assert workspace.embeddings is provider
    assert not first.root.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"embedding_dimension": 384},
        {"embedding_dimension": 0, "embedding_fingerprint": "1" * 64},
        {"embedding_dimension": True, "embedding_fingerprint": "1" * 64},
        {"embedding_dimension": 384.0, "embedding_fingerprint": "1" * 64},
        {"embedding_dimension": 4097, "embedding_fingerprint": "1" * 64},
        {"embedding_fingerprint": "main"},
    ],
)
def test_invalid_embedding_configuration_rejects_before_creating_resources(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    with pytest.raises((ValidationError, AuditWorkspaceError)):
        configuration(tmp_path, **changes)
    assert not (tmp_path / "ragelit_audit_unit").exists()


def test_unbound_custom_provider_rejects_before_resource_access(tmp_path: Path) -> None:
    config = configuration(tmp_path)
    with pytest.raises(AuditWorkspaceError, match="audit_embedding_mismatch"):
        AuditWorkspace(config, generate_fixtures(), embeddings=MissEmbeddings())
    assert not config.root.exists()


def test_bound_provider_must_be_supplied(tmp_path: Path) -> None:
    config = configuration(
        tmp_path, embedding_fingerprint="1" * 64, embedding_dimension=384
    )
    with pytest.raises(AuditWorkspaceError, match="audit_embedding_mismatch"):
        AuditWorkspace(config, generate_fixtures())
    assert not config.root.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"embedding_fingerprint": "2" * 64, "embedding_dimension": 384},
        {"embedding_fingerprint": "1" * 64, "embedding_dimension": 128},
    ],
)
def test_wrong_provider_identity_rejects_before_resource_access(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    config = configuration(tmp_path, **changes)
    with pytest.raises(AuditWorkspaceError, match="audit_embedding_mismatch"):
        AuditWorkspace(config, generate_fixtures(), embeddings=MissEmbeddings())
    assert not config.root.exists()


def test_copied_invalid_configuration_does_not_bypass_binding_guards(
    tmp_path: Path,
) -> None:
    config = configuration(tmp_path).model_copy(
        update={"embedding_dimension": 384, "embedding_fingerprint": None}
    )
    with pytest.raises((ValidationError, AuditWorkspaceError)):
        AuditWorkspace(config, generate_fixtures(), embeddings=MissEmbeddings())
    assert not config.root.exists()


def test_copied_production_settings_reject_before_resource_access(
    tmp_path: Path,
) -> None:
    config = configuration(tmp_path).model_copy(update={"environment": "production"})
    with pytest.raises(ValidationError, match="environment"):
        AuditWorkspace(config, generate_fixtures())
    assert not config.root.exists()


@pytest.mark.parametrize("strategy", ["shared_pre_filter", "tenant_collections"])
def test_real_collection_geometry_follows_the_bound_provider(
    tmp_path: Path, strategy: str
) -> None:
    config = configuration(
        tmp_path,
        vector_strategy=strategy,
        embedding_fingerprint="1" * 64,
        embedding_dimension=384,
    )
    workspace = AuditWorkspace(config, generate_fixtures(), embeddings=MissEmbeddings())
    client = QdrantClient(":memory:")
    try:
        workspace._vector_client = client
        org = UUID(int=1)
        workspace.bindings = workspace.bindings.model_copy(
            update={"organizations": {"org-1": org}}
        )
        store = workspace._make_store()
        if strategy == "shared_pre_filter":
            store.ensure_collection()
        else:
            store.ensure_tenants((org,))
        for name in store.collection_names():
            info = client.get_collection(name)
            dense = info.config.params.vectors
            assert isinstance(dense, dict)
            assert dense["dense"].size == 384
            assert dense["dense"].distance == models.Distance.COSINE
            sparse = info.config.params.sparse_vectors
            assert sparse is not None
            assert sparse["sparse"].modifier == models.Modifier.IDF
    finally:
        client.close()


def test_utility_template_preserves_exact_documents_questions_and_grants() -> None:
    corpus = generate_utility_corpus()
    template = utility_template(corpus)
    assert template.generator_id == "natural-utility-v1"
    assert template.pack_id == "utility-v1"
    assert template.seed == 20261005
    assert template.organizations == corpus.organizations
    assert template.groups == corpus.groups
    assert template.actors == corpus.actors
    assert len(template.documents) == 87
    queries = {query.relevant_document_ids[0]: query for query in corpus.queries}
    for source, actual in zip(corpus.documents, template.documents, strict=True):
        assert actual.id == source.id
        assert actual.organization_id == source.organization_id
        assert actual.text == source.text
        assert actual.visibility == source.visibility
        assert actual.user_ids == source.user_ids
        assert actual.group_ids == source.group_ids
        assert actual.question == queries[source.id].question
        assert actual.control_actor_id == queries[source.id].actor_id
    assert (
        sum(doc.kind == DocumentKind.ORGANIZATION for doc in template.documents) == 60
    )
    assert sum(doc.kind == DocumentKind.GROUP for doc in template.documents) == 24
    assert sum(doc.kind == DocumentKind.USER for doc in template.documents) == 3
    assert template.cases == ()
    assert template.canaries == {}


def test_modified_utility_labels_reject_before_workspace_creation() -> None:
    corpus = generate_utility_corpus()
    query = corpus.queries[0].model_copy(update={"question": "A tuned question?"})
    changed = corpus.model_copy(update={"queries": (query, *corpus.queries[1:])})
    with pytest.raises(ValueError, match="utility_corpus_drift"):
        utility_template(changed)


def test_invalid_copied_utility_grants_are_revalidated() -> None:
    corpus = generate_utility_corpus()
    source = corpus.documents[0]
    document = source.model_copy(update={"organization_id": "foreign"})
    changed = corpus.model_copy(update={"documents": (document, *corpus.documents[1:])})
    with pytest.raises(ValidationError):
        utility_template(changed)


def test_utility_template_cannot_be_used_as_a_security_pack(tmp_path: Path) -> None:
    template = utility_template(generate_utility_corpus())
    workspace = AuditWorkspace(configuration(tmp_path), template)
    with pytest.raises(AuditWorkspaceError, match="audit_fixture_pack_required"):
        prepare_pack(workspace, uuid4())


def test_original_access_and_injection_manifests_remain_unchanged() -> None:
    assert (
        generate_fixtures().checksum
        == "4949502da16d689928823c2ecdc22647423f92fbf6a317e2856cefa6d51e0277"
    )
    assert (
        generate_injection_fixtures().checksum
        == "7e24c309ee05f5fc406e8dd8e092612db4415ef49499fba2ae27787ccfa909a2"
    )
