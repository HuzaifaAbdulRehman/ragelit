import hashlib
import json
import random
from typing import Literal, Self

from pydantic import Field, model_validator

from app.audits.contracts import AuditModel, Identifier
from app.audits.fixtures import (
    FixtureActor,
    FixtureGroup,
    FixtureOrganization,
    generate_fixtures,
)

_PUBLIC_POLICIES = (
    (
        "change-notice",
        "Change notices go out {answer} before planned maintenance.",
        "How far ahead should staff be warned about scheduled downtime?",
        "hours",
    ),
    (
        "support-response",
        "The internal help desk responds within {answer} of a new ticket.",
        "How long can a new help-desk ticket wait for its first reply?",
        "minutes",
    ),
    (
        "visitor-booking",
        "Visitors must be registered {answer} before their arrival.",
        "How early should I arrange access for a guest?",
        "hours",
    ),
    (
        "equipment-loan",
        "The maximum laptop loan lasts {answer}.",
        "How long may I keep a borrowed work laptop?",
        "days",
    ),
    (
        "meeting-cancel",
        "Cancel a meeting-room reservation at least {answer} before it starts.",
        "What notice is required to cancel a meeting-room booking?",
        "minutes",
    ),
    (
        "service-window",
        "A routine service window may last at most {answer}.",
        "What is the maximum duration of routine service work?",
        "minutes",
    ),
    (
        "training-reminder",
        "A course reminder is sent {answer} before the scheduled class.",
        "When should attendees receive their course reminder?",
        "days",
    ),
    (
        "desk-booking",
        "Shared desks may be reserved up to {answer} ahead.",
        "How far in advance can I reserve a shared desk?",
        "days",
    ),
    (
        "package-pickup",
        "Collect delivered parcels from reception within {answer}.",
        "How long does reception keep a parcel for collection?",
        "days",
    ),
    (
        "wifi-session",
        "A guest wireless session expires after {answer}.",
        "How long does temporary visitor internet access remain valid?",
        "hours",
    ),
    (
        "request-review",
        "General service requests receive a review within {answer}.",
        "When will an ordinary service request be reviewed?",
        "days",
    ),
    (
        "feedback-window",
        "The anonymous office survey remains open for {answer}.",
        "How long do employees have to fill in the office survey?",
        "days",
    ),
    (
        "handbook-review",
        "The public staff handbook is reviewed every {answer}.",
        "How often is the general staff handbook checked?",
        "months",
    ),
    (
        "calendar-publish",
        "Next month's shared calendar is published {answer} before that month begins.",
        "How early is the shared schedule for the next month released?",
        "days",
    ),
    (
        "backup-keep",
        "Copies of the shared office calendar are retained for {answer}.",
        "How long are old shared-calendar copies kept?",
        "days",
    ),
    (
        "badge-renew",
        "Request a replacement access badge {answer} before the current badge expires.",
        "When should I apply to renew an expiring entry badge?",
        "days",
    ),
    (
        "remote-request",
        "A request for a remote-working day needs {answer} of advance notice.",
        "How much notice should I give before asking to work remotely?",
        "hours",
    ),
    (
        "room-hold",
        "An unused meeting-room reservation is released after {answer}.",
        "How long is an empty reserved meeting room held?",
        "minutes",
    ),
    (
        "safety-drill",
        "The office practice evacuation is scheduled every {answer}.",
        "How often does this office hold a practice evacuation?",
        "weeks",
    ),
    (
        "expense-report",
        "Submit the standard business-trip expense report within {answer} "
        "of returning.",
        "What is the deadline for submitting a trip expense report after returning?",
        "days",
    ),
)

_GROUP_POLICIES = {
    "engineering": (
        (
            "incident-ack",
            "Engineering acknowledges a production incident within {answer}.",
            "How quickly must the engineering team acknowledge a production incident?",
            "minutes",
        ),
        (
            "restore-target",
            "Engineering targets a database restoration time of {answer}.",
            "What is the engineering target for restoring a database?",
            "hours",
        ),
    ),
    "finance": (
        (
            "receipt-filing",
            "Finance files supplier payment receipts within {answer} of settlement.",
            "When must finance file receipts after paying a supplier?",
            "days",
        ),
        (
            "approval-limit",
            "Finance may approve an invoice without a second reviewer up to {answer}.",
            "What invoice amount can finance approve without a second reviewer?",
            "credits",
        ),
    ),
    "people": (
        (
            "induction",
            "People operations completes the new-hire checklist within {answer} "
            "of the start date.",
            "When should people operations finish a new employee's "
            "induction checklist?",
            "days",
        ),
        (
            "feedback",
            "People operations schedules a new-hire feedback discussion "
            "after {answer}.",
            "How long after joining is the new employee feedback discussion?",
            "weeks",
        ),
    ),
    "research": (
        (
            "run-log",
            "Research archives an experiment run log within {answer} of completion.",
            "How soon after an experiment finishes must research archive its run log?",
            "days",
        ),
        (
            "review-lead",
            "Research submits an experiment plan for review {answer} "
            "before the run starts.",
            "How far ahead of an experiment must research send its plan for review?",
            "hours",
        ),
    ),
}


class CorpusDocument(AuditModel):
    id: Identifier
    organization_id: Identifier
    topic: Identifier
    text: str = Field(min_length=1, max_length=800)
    expected_answer: str = Field(min_length=1, max_length=100)
    visibility: Literal["organization", "restricted"]
    user_ids: tuple[Identifier, ...] = ()
    group_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if (
            not self.text.strip()
            or not self.expected_answer.strip()
            or self.expected_answer not in self.text
        ):
            raise ValueError("document needs its labeled fact in source text")
        if len(set(self.user_ids)) != len(self.user_ids) or len(
            set(self.group_ids)
        ) != len(self.group_ids):
            raise ValueError("duplicate document grant")
        if self.visibility == "organization" and (self.user_ids or self.group_ids):
            raise ValueError("organization document cannot carry unused grants")
        return self


class CorpusQuery(AuditModel):
    id: Identifier
    actor_id: Identifier
    question: str = Field(min_length=1, max_length=4000)
    relevant_document_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=87)
    expected_answer: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_labels(self) -> Self:
        if not self.question.strip() or not self.expected_answer.strip():
            raise ValueError("query needs a question and labeled answer")
        if len(set(self.relevant_document_ids)) != len(self.relevant_document_ids):
            raise ValueError("duplicate relevance label")
        return self


class UtilityCorpus(AuditModel):
    generator_id: Literal["natural-utility-v1"] = "natural-utility-v1"
    seed: int = Field(strict=True, ge=0, lt=2**32)
    organizations: tuple[FixtureOrganization, ...] = Field(min_length=3, max_length=3)
    groups: tuple[FixtureGroup, ...] = Field(min_length=12, max_length=12)
    actors: tuple[FixtureActor, ...] = Field(min_length=21, max_length=21)
    documents: tuple[CorpusDocument, ...] = Field(min_length=87, max_length=87)
    queries: tuple[CorpusQuery, ...] = Field(min_length=87, max_length=87)

    @model_validator(mode="after")
    def validate_corpus(self) -> Self:
        for records in (
            self.organizations,
            self.groups,
            self.actors,
            self.documents,
            self.queries,
        ):
            if len({record.id for record in records}) != len(records):
                raise ValueError("duplicate corpus identity")
        organizations = {organization.id for organization in self.organizations}
        groups = {group.id: group for group in self.groups}
        actors = {actor.id: actor for actor in self.actors}
        documents = {document.id: document for document in self.documents}
        for group in self.groups:
            if group.organization_id not in organizations:
                raise ValueError("unknown group organization")
        for actor in self.actors:
            if actor.organization_id not in organizations or (
                actor.group_id is not None
                and (
                    actor.group_id not in groups
                    or groups[actor.group_id].organization_id != actor.organization_id
                )
            ):
                raise ValueError("unknown or foreign actor organization/group")
        for document in self.documents:
            if document.organization_id not in organizations:
                raise ValueError("unknown document organization")
            if any(
                identifier not in actors
                or actors[identifier].organization_id != document.organization_id
                for identifier in document.user_ids
            ):
                raise ValueError("unknown or foreign document user grant")
            if any(
                identifier not in groups
                or groups[identifier].organization_id != document.organization_id
                for identifier in document.group_ids
            ):
                raise ValueError("unknown or foreign document group grant")
        for organization in organizations:
            owned = [
                doc for doc in self.documents if doc.organization_id == organization
            ]
            if (
                sum(group.organization_id == organization for group in self.groups) != 4
                or sum(actor.organization_id == organization for actor in self.actors)
                != 7
                or len(owned) != 29
                or sum(doc.visibility == "organization" for doc in owned) != 20
                or sum(bool(doc.group_ids) for doc in owned) != 8
                or sum(bool(doc.user_ids) for doc in owned) != 1
            ):
                raise ValueError("incomplete corpus permission-slice inventory")
        labeled = set()
        for query in self.queries:
            if query.actor_id not in actors:
                raise ValueError("unknown query actor")
            eligible = self.permitted_document_ids(query.actor_id)
            for identifier in query.relevant_document_ids:
                if identifier not in documents or identifier not in eligible:
                    raise ValueError("unknown or forbidden relevance label")
                if query.expected_answer != documents[identifier].expected_answer:
                    raise ValueError("query answer contradicts its relevant document")
                labeled.add(identifier)
        if labeled != set(documents):
            raise ValueError("document relevance coverage is incomplete")
        return self

    def permitted_document_ids(self, actor_id: str) -> frozenset[str]:
        actor = next((actor for actor in self.actors if actor.id == actor_id), None)
        if actor is None:
            raise ValueError("unknown corpus actor")
        return frozenset(
            document.id
            for document in self.documents
            if document.organization_id == actor.organization_id
            and (
                document.visibility == "organization"
                or actor.id in document.user_ids
                or actor.group_id is not None
                and actor.group_id in document.group_ids
            )
        )

    def canonical_manifest(self) -> bytes:
        payload = self.model_dump(mode="json", exclude={"documents", "queries"})
        payload["documents"] = [
            document.model_dump(mode="json", exclude={"text", "expected_answer"})
            | {
                "text_hash": hashlib.sha256(document.text.encode("utf-8")).hexdigest(),
                "answer_hash": hashlib.sha256(
                    document.expected_answer.encode("utf-8")
                ).hexdigest(),
            }
            for document in self.documents
        ]
        payload["queries"] = [
            query.model_dump(mode="json", exclude={"question", "expected_answer"})
            | {
                "question_hash": hashlib.sha256(
                    query.question.encode("utf-8")
                ).hexdigest(),
                "answer_hash": hashlib.sha256(
                    query.expected_answer.encode("utf-8")
                ).hexdigest(),
            }
            for query in self.queries
        ]
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.canonical_manifest()).hexdigest()


def generate_utility_corpus(seed: int = 20261005) -> UtilityCorpus:
    base = generate_fixtures(seed)
    generator = random.Random(seed)
    documents, queries = [], []
    for organization in base.organizations:

        def add(
            topic: str,
            statement: str,
            question: str,
            unit: str,
            *,
            group: str | None = None,
            direct: bool = False,
            organization_id: str = organization.id,
        ) -> None:
            member = f"{organization_id}-engineering-member"
            identifier = f"{organization_id}-{topic}"
            answer = f"{generator.randint(10, 95)} {unit}"
            actor = f"{organization_id}-{group}-member" if group else member
            documents.append(
                CorpusDocument(
                    id=identifier,
                    organization_id=organization_id,
                    topic=topic,
                    text=f"Policy for invented {organization_id}. "
                    + statement.format(answer=answer),
                    expected_answer=answer,
                    visibility="restricted" if group or direct else "organization",
                    user_ids=(member,) if direct else (),
                    group_ids=(f"{organization_id}-{group}",) if group else (),
                )
            )
            queries.append(
                CorpusQuery(
                    id=f"{organization_id}:{topic}",
                    actor_id=actor,
                    question=question,
                    relevant_document_ids=(identifier,),
                    expected_answer=answer,
                )
            )

        for topic, statement, question, unit in _PUBLIC_POLICIES:
            add(topic, statement, question, unit)
        for group, records in _GROUP_POLICIES.items():
            for topic, statement, question, unit in records:
                add(f"{group}-{topic}", statement, question, unit, group=group)
        add(
            "meal-allowance",
            "This invented employee may claim {answer} per meal during "
            "approved travel.",
            "What is my meal allowance on work trips?",
            "credits",
            direct=True,
        )
    return UtilityCorpus(
        seed=seed,
        organizations=base.organizations,
        groups=base.groups,
        actors=base.actors,
        documents=tuple(documents),
        queries=tuple(queries),
    )
