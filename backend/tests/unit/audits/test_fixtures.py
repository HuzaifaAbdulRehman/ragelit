import hashlib
import json
import math

import pytest

from app.audits.embeddings import FixtureEmbeddings
from app.audits.fixtures import generate_fixtures


def test_template_covers_three_tenants_roles_groups_and_document_classes() -> None:
    template = generate_fixtures()
    assert len(template.organizations) == 3
    for organization in template.organizations:
        groups = [g for g in template.groups if g.organization_id == organization.id]
        assert len(groups) == 4
        actors = [a for a in template.actors if a.organization_id == organization.id]
        assert {a.role.value for a in actors} == {"owner", "admin", "auditor", "member"}
        assert len([a for a in actors if a.role.value == "member"]) == 4
        assert all(a.email.endswith("@example.invalid") for a in actors)
        documents = [
            d for d in template.documents if d.organization_id == organization.id
        ]
        assert {d.kind.value for d in documents} == {
            "anchor",
            "organization",
            "group",
            "user",
            "ungranted",
            "revoked",
            "deleted",
            "superseded",
            "poisoned",
        }


def test_checksums_are_stable_and_cover_content_without_storing_credentials() -> None:
    first = generate_fixtures()
    assert first.checksum == generate_fixtures().checksum
    assert first.checksum != generate_fixtures(7).checksum
    assert first.checksum == hashlib.sha256(first.canonical_manifest()).hexdigest()
    manifest = json.loads(first.canonical_manifest())
    serialized = json.dumps(manifest)
    for field in ("password", "access_token", "refresh_token", "secret_key"):
        assert f'"{field}"' not in serialized.lower()
    assert len(first.canaries) == len(first.documents)
    assert len(set(first.canaries.values())) == len(first.documents)
    for document in first.documents:
        assert document.canary in document.text
        assert document.canary not in serialized
        assert document.text not in serialized
        assert len(document.text) < 1200
    changed = first.model_copy(
        update={
            "documents": (
                first.documents[0].model_copy(update={"text": "different content"}),
                *first.documents[1:],
            )
        }
    )
    assert changed.checksum != first.checksum


def test_required_inventory_has_every_control_and_independent_positive_labels() -> None:
    template = generate_fixtures()
    assert len(template.cases) == 51
    assert len({case.id for case in template.cases}) == 51
    expected = {
        "organization_access",
        "direct_user_access",
        "group_access",
        "tenant_isolation",
        "role_read_separation",
        "forged_metadata",
        "revoked_membership",
        "revoked_grant_or_group",
        "deleted_document",
        "superseded_version",
        "citation_boundary",
    }
    assert {case.control.value for case in template.cases} == expected
    actors = {actor.id: actor for actor in template.actors}
    documents = {document.id: document for document in template.documents}
    for case in template.cases:
        assert case.actor_id in actors
        assert case.document_id in documents
        if case.positive:
            assert case.required_document_id == case.document_id
            assert case.document_id not in case.forbidden_document_ids
        else:
            assert case.required_document_id is None
            assert case.document_id in case.forbidden_document_ids
    assert sum(case.positive for case in template.cases) == 12
    assert sum(case.action == "revoke_group" for case in template.cases) == 3
    assert sum(case.action == "replace" for case in template.cases) == 6


def test_fixture_vectors_rank_each_probe_above_permitted_anchor() -> None:
    template = generate_fixtures()
    provider = FixtureEmbeddings()
    vectors = provider.documents([document.text for document in template.documents])
    assert vectors == provider.documents(
        [document.text for document in template.documents]
    )
    assert len({vector.dense for vector in vectors}) == len(vectors)
    for document, vector in zip(template.documents, vectors, strict=True):
        assert len(vector.dense) == provider.dimension
        assert math.isclose(sum(v * v for v in vector.dense), 1.0)
        if document.kind.value == "anchor":
            continue
        anchor_index = next(
            i
            for i, candidate in enumerate(template.documents)
            if candidate.organization_id == document.organization_id
            and candidate.kind.value == "anchor"
        )
        anchor = vectors[anchor_index]
        query = provider.query(document.question)
        dense_score = sum(a * b for a, b in zip(query.dense, vector.dense, strict=True))
        anchor_score = sum(
            a * b for a, b in zip(query.dense, anchor.dense, strict=True)
        )
        assert dense_score > anchor_score
        query_sparse = dict(zip(query.indices, query.values, strict=True))
        probe_sparse = sum(
            value * query_sparse.get(index, 0)
            for index, value in zip(vector.indices, vector.values, strict=True)
        )
        anchor_sparse = sum(
            value * query_sparse.get(index, 0)
            for index, value in zip(anchor.indices, anchor.values, strict=True)
        )
        assert probe_sparse > anchor_sparse


@pytest.mark.parametrize("seed", [-1, 2**32, True])
def test_generator_rejects_invalid_seed(seed: int) -> None:
    with pytest.raises(ValueError, match="invalid fixture seed"):
        generate_fixtures(seed)


@pytest.mark.parametrize(
    "text", ["no fixture topic", "audittopic99", "audittopic1 audittopic2"]
)
def test_fixture_embeddings_reject_unregistered_or_ambiguous_topics(text: str) -> None:
    with pytest.raises(ValueError, match="invalid fixture topic"):
        FixtureEmbeddings().query(text)
