import hashlib
import json

import pytest

from app.audits.embeddings import FixtureEmbeddings
from app.audits.fixtures import generate_fixtures
from app.audits.injection_fixtures import fact_marker, generate_injection_fixtures


def test_fixture_inventory_is_pinned_and_access_pack_unchanged() -> None:
    template = generate_injection_fixtures(trials=2)
    assert template.pack_id == "injection-v1"
    assert len(template.documents) == 6
    assert len(template.organizations) == 3
    assert len(template.groups) == 12
    assert len(template.cases) == 12
    assert len({case.id for case in template.cases}) == 12
    assert sum("attack" in case.id for case in template.cases) == 6
    assert all(case.positive and case.action == "query" for case in template.cases)
    assert (
        generate_fixtures().checksum
        == "4949502da16d689928823c2ecdc22647423f92fbf6a317e2856cefa6d51e0277"
    )


def test_fixture_hash_covers_versioned_content_without_serializing_markers() -> None:
    first = generate_injection_fixtures()
    assert first.checksum == generate_injection_fixtures().checksum
    assert first.checksum != generate_injection_fixtures(seed=7).checksum
    assert first.checksum != generate_injection_fixtures(trials=2).checksum
    serialized = first.canonical_manifest().decode()
    assert "override-v1" in first.documents[1].text
    assert "system-message-v1" in first.documents[3].text
    assert "evaluation-pressure-v1" in first.documents[5].text
    for document in first.documents:
        fact_id, fact = fact_marker(first.seed, document.id)
        assert fact in document.text
        assert fact_id.endswith("-fact")
        assert fact not in serialized
        assert document.canary not in serialized
        assert len(document.text) < 1200
    manifest = json.loads(serialized)
    for document, entry in zip(first.documents, manifest["documents"], strict=True):
        assert (
            entry["content_hash"] == hashlib.sha256(document.text.encode()).hexdigest()
        )


def test_fixture_topics_remain_compatible_and_queries_do_not_contain_answers() -> None:
    template = generate_injection_fixtures()
    vectors = FixtureEmbeddings().documents([doc.text for doc in template.documents])
    assert tuple(doc.text.split()[0] for doc in template.documents) == (
        "audittopic1",
        "audittopic8",
        "audittopic10",
        "audittopic17",
        "audittopic19",
        "audittopic26",
    )
    assert len({vector.dense for vector in vectors}) == 6
    for doc, vector in zip(template.documents, vectors, strict=True):
        assert FixtureEmbeddings().query(doc.question).dense == vector.dense
        assert doc.canary not in doc.question
        assert fact_marker(template.seed, doc.id)[1] not in doc.question
        group = "engineering" if doc.kind.value == "poisoned" else "finance"
        assert doc.control_actor_id == f"{doc.organization_id}-{group}-member"


@pytest.mark.parametrize("trials", [0, 21, True, 1.5])
def test_trial_count_is_bounded_and_not_coerced(trials: int) -> None:
    with pytest.raises(ValueError, match="invalid injection trials"):
        generate_injection_fixtures(trials=trials)
