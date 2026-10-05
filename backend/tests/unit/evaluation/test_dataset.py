import hashlib
import json
import random

import pytest

from app.evaluation.dataset import UtilityCorpus, generate_utility_corpus

PUBLIC_TOPICS = (
    "change-notice",
    "support-response",
    "visitor-booking",
    "equipment-loan",
    "meeting-cancel",
    "service-window",
    "training-reminder",
    "desk-booking",
    "package-pickup",
    "wifi-session",
    "request-review",
    "feedback-window",
    "handbook-review",
    "calendar-publish",
    "backup-keep",
    "badge-renew",
    "remote-request",
    "room-hold",
    "safety-drill",
    "expense-report",
)


def test_full_inventory_has_natural_language_and_all_permission_slices() -> None:
    corpus = generate_utility_corpus()
    assert len(corpus.organizations) == 3
    assert len(corpus.groups) == 12
    assert len(corpus.actors) == 21
    assert len(corpus.documents) == len(corpus.queries) == 87
    for organization in ("org-1", "org-2", "org-3"):
        documents = [
            doc for doc in corpus.documents if doc.organization_id == organization
        ]
        assert len(documents) == 29
        assert sum(doc.visibility == "organization" for doc in documents) == 20
        assert sum(bool(doc.group_ids) for doc in documents) == 8
        assert sum(bool(doc.user_ids) for doc in documents) == 1
    for document in corpus.documents:
        assert "audittopic" not in document.text
        assert "AUDITCANARY" not in document.text
        assert len(document.text) < 800
    for query in corpus.queries:
        assert "audittopic" not in query.question
        assert query.expected_answer not in query.question
        assert len(query.relevant_document_ids) == 1


def test_first_question_has_an_authored_paraphrase_and_relevance_label() -> None:
    corpus = generate_utility_corpus()
    first = corpus.queries[0]
    assert first.actor_id == "org-1-engineering-member"
    assert (
        first.question
        == "How far ahead should staff be warned about scheduled downtime?"
    )
    assert first.relevant_document_ids == ("org-1-change-notice",)
    document = corpus.documents[0]
    assert document.id == "org-1-change-notice"
    assert document.expected_answer.endswith(" hours")
    assert first.expected_answer == document.expected_answer
    assert first.expected_answer in document.text


@pytest.mark.parametrize(
    "actor", ["owner", "admin", "auditor", "engineering-member", "finance-member"]
)
def test_declared_eligibility_has_no_role_bypass_or_foreign_organization(
    actor: str,
) -> None:
    corpus = generate_utility_corpus()
    expected = {f"org-1-{topic}" for topic in PUBLIC_TOPICS}
    if actor == "engineering-member":
        expected |= {
            "org-1-engineering-incident-ack",
            "org-1-engineering-restore-target",
            "org-1-meal-allowance",
        }
    elif actor == "finance-member":
        expected |= {"org-1-finance-receipt-filing", "org-1-finance-approval-limit"}
    assert corpus.permitted_document_ids(f"org-1-{actor}") == frozenset(expected)


def test_unknown_actor_does_not_receive_public_labels() -> None:
    with pytest.raises(ValueError):
        generate_utility_corpus().permitted_document_ids("unknown-actor")


def test_corpus_is_repeatable_without_changing_global_rng() -> None:
    before = random.getstate()
    first, second = generate_utility_corpus(), generate_utility_corpus()
    assert first == second
    assert first.canonical_manifest() == second.canonical_manifest()
    assert first.checksum == second.checksum
    assert random.getstate() == before
    alternate = generate_utility_corpus(20261006)
    assert alternate.checksum != first.checksum
    assert any(
        left.expected_answer != right.expected_answer
        for left, right in zip(first.documents, alternate.documents, strict=True)
    )


def test_manifest_hashes_sensitive_fields_and_retains_effective_labels() -> None:
    corpus = generate_utility_corpus()
    manifest = corpus.canonical_manifest()
    decoded = json.loads(manifest)
    document, query = corpus.documents[0], corpus.queries[0]
    assert decoded["generator_id"] == "natural-utility-v1"
    assert decoded["seed"] == 20261005
    assert (
        decoded["documents"][0]["text_hash"]
        == hashlib.sha256(document.text.encode()).hexdigest()
    )
    assert (
        decoded["queries"][0]["question_hash"]
        == hashlib.sha256(query.question.encode()).hexdigest()
    )
    assert decoded["queries"][0]["relevant_document_ids"] == ["org-1-change-notice"]
    assert document.text.encode() not in manifest
    assert query.question.encode() not in manifest
    assert query.expected_answer.encode() not in manifest
    assert corpus.checksum == hashlib.sha256(manifest).hexdigest()


@pytest.mark.parametrize(
    "field", ["text", "answer", "question", "qrel", "grant", "actor"]
)
def test_every_effective_input_changes_the_manifest(field: str) -> None:
    corpus = generate_utility_corpus()
    if field in {"text", "answer", "grant"}:
        updates: dict[str, object] = {"text": "Changed source text."}
        if field == "answer":
            updates = {"expected_answer": "Changed label"}
        elif field == "grant":
            updates = {"user_ids": ("org-1-owner",)}
        document = corpus.documents[0].model_copy(update=updates)
        changed = corpus.model_copy(
            update={"documents": (document, *corpus.documents[1:])}
        )
    elif field in {"question", "qrel"}:
        query_updates: dict[str, object] = {"question": "A different question?"}
        if field == "qrel":
            query_updates = {"relevant_document_ids": ("org-1-support-response",)}
        query = corpus.queries[0].model_copy(update=query_updates)
        changed = corpus.model_copy(update={"queries": (query, *corpus.queries[1:])})
    else:
        actor = corpus.actors[0].model_copy(update={"email": "changed@example.invalid"})
        changed = corpus.model_copy(update={"actors": (actor, *corpus.actors[1:])})
    assert changed.checksum != corpus.checksum


@pytest.mark.parametrize("seed", [-1, 2**32, True])
def test_invalid_seed_is_not_coerced_to_another_corpus(seed: int) -> None:
    with pytest.raises(ValueError):
        generate_utility_corpus(seed)


@pytest.mark.parametrize(
    "defect",
    [
        "missing_query",
        "duplicate_query",
        "duplicate_document",
        "unknown_actor",
        "foreign_actor",
        "role_bypass",
        "foreign_user_grant",
        "foreign_group_grant",
        "unknown_user_grant",
        "foreign_actor_group",
        "unknown_document_organization",
        "empty_labels",
        "unknown_relevant_document",
        "duplicate_labels",
        "contradictory_answer",
        "missing_fact",
        "blank_question",
        "blank_answer",
        "boolean_seed",
        "duplicate_group_grant",
        "public_with_unused_grant",
        "missing_group",
        "duplicate_actor",
        "incomplete_relevance_coverage",
        "unknown_actor_group",
    ],
)
def test_invalid_corpus_cannot_change_the_benchmark_denominator_or_labels(
    defect: str,
) -> None:
    payload = generate_utility_corpus().model_dump(mode="json")
    if defect == "missing_query":
        payload["queries"].pop()
    elif defect == "duplicate_query":
        payload["queries"][0] = payload["queries"][1]
    elif defect == "duplicate_document":
        payload["documents"][0] = payload["documents"][1]
    elif defect == "unknown_actor":
        payload["queries"][0]["actor_id"] = "missing-actor"
    elif defect == "foreign_actor":
        payload["queries"][0]["actor_id"] = "org-2-engineering-member"
    elif defect == "role_bypass":
        payload["queries"][20]["actor_id"] = "org-1-owner"
    elif defect == "foreign_user_grant":
        payload["documents"][28]["user_ids"] = ["org-2-engineering-member"]
    elif defect == "foreign_group_grant":
        payload["documents"][20]["group_ids"] = ["org-2-engineering"]
    elif defect == "unknown_user_grant":
        payload["documents"][28]["user_ids"] = ["missing-actor"]
    elif defect == "foreign_actor_group":
        payload["actors"][0]["group_id"] = "org-2-engineering"
    elif defect == "unknown_document_organization":
        payload["documents"][0]["organization_id"] = "missing-organization"
    elif defect == "empty_labels":
        payload["queries"][0]["relevant_document_ids"] = []
    elif defect == "unknown_relevant_document":
        payload["queries"][0]["relevant_document_ids"] = ["missing-document"]
    elif defect == "duplicate_labels":
        payload["queries"][0]["relevant_document_ids"] *= 2
    elif defect == "contradictory_answer":
        payload["queries"][0]["expected_answer"] = "An unsupported fact"
    elif defect == "missing_fact":
        payload["documents"][0]["text"] = "No labeled answer appears in this policy."
    elif defect == "blank_question":
        payload["queries"][0]["question"] = "   "
    elif defect == "blank_answer":
        payload["documents"][0]["expected_answer"] = "   "
    elif defect == "boolean_seed":
        payload["seed"] = True
    elif defect == "duplicate_group_grant":
        payload["documents"][20]["group_ids"] *= 2
    elif defect == "public_with_unused_grant":
        payload["documents"][0]["user_ids"] = ["org-1-owner"]
    elif defect == "missing_group":
        payload["groups"].pop()
    elif defect == "duplicate_actor":
        payload["actors"][0] = payload["actors"][1]
    elif defect == "incomplete_relevance_coverage":
        payload["queries"][0]["relevant_document_ids"] = payload["queries"][1][
            "relevant_document_ids"
        ]
        payload["queries"][0]["expected_answer"] = payload["queries"][1][
            "expected_answer"
        ]
    elif defect == "unknown_actor_group":
        payload["actors"][0]["group_id"] = "missing-group"
    else:
        raise AssertionError("Unimplemented defect fixture")
    with pytest.raises(ValueError):
        UtilityCorpus.model_validate(payload)
