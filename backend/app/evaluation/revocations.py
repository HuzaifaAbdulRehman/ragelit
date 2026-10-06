import hashlib
from time import perf_counter
from typing import Literal, Self

from pydantic import Field, model_validator
from sqlalchemy import select

from app.audits.contracts import AuditModel, Boundary, BoundaryEvidence, Terminal
from app.audits.seeding import login_actor
from app.audits.workspace import AuditWorkspace
from app.chat.contracts import GenerationProvider
from app.documents.models import Document, DocumentGrant
from app.evaluation.dataset import CorpusQuery, UtilityCorpus, generate_utility_corpus
from app.evaluation.reports import UtilityDocumentBinding, UtilityQueryRecord
from app.evaluation.runner import _corpus, capture_utility_query
from app.retrieval.store import QdrantChunkStore

_ERROR = "utility_revocation_invalid"
GrantRestore = Literal["not_needed", "restored", "failed"]


def _stage(record: UtilityQueryRecord, boundary: Boundary) -> BoundaryEvidence | None:
    return next(
        (
            stage
            for stage in record.observation.boundaries
            if stage.boundary == boundary
        ),
        None,
    )


class RevocationMeasurement(AuditModel):
    target: UtilityDocumentBinding
    before: UtilityQueryRecord
    after: UtilityQueryRecord | None = None
    update_committed: bool = Field(default=False, strict=True)
    elapsed_ms: float | None = Field(default=None, strict=True, ge=0)
    grant_restore: GrantRestore = "not_needed"
    runtime_failed: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        corpus = generate_utility_corpus()
        document = next(
            (doc for doc in corpus.documents if doc.id == self.target.logical_id), None
        )
        query = next(
            (item for item in corpus.queries if item.id == self.before.query_id), None
        )
        if (
            document is None
            or query is None
            or self.target.content_hash
            != hashlib.sha256(document.text.encode()).hexdigest()
            or len(set(self.target.chunk_ids)) != len(self.target.chunk_ids)
            or query.relevant_document_ids != (self.target.logical_id,)
            or not self.update_committed
            and (self.after is not None or self.elapsed_ms is not None)
            or self.elapsed_ms is not None
            and self.after is None
        ):
            raise ValueError(_ERROR)
        for record in (self.before, self.after):
            if record is not None and (
                record.query_id != query.id
                or record.actor_id != query.actor_id
                or record.relevant_document_ids != query.relevant_document_ids
                or record.observation.case_id != query.id
            ):
                raise ValueError(_ERROR)
        return self

    @property
    def positive_before(self) -> bool:
        observation = self.before.observation
        if (
            observation.http_status != 200
            or observation.terminal != Terminal.ANSWERED
            or observation.scope_hash is None
            or self.before.error_code is not None
            or self.before.answer_label_match is not True
            or self.target.version_id not in self.before.permitted_version_ids
        ):
            return False
        candidates = set(self.target.chunk_ids)
        for boundary in (
            Boundary.RETRIEVAL_ACCEPTED,
            Boundary.CONTEXT,
            Boundary.CITATIONS_DELIVERED,
        ):
            stage = _stage(self.before, boundary)
            if stage is None or stage.state != "observed" or stage.truncated:
                return False
            candidates.intersection_update(stage.chunk_ids)
        return any(
            citation.chunk_id in candidates
            and citation.document_id == self.target.document_id
            and citation.version_id == self.target.version_id
            for citation in self.before.citations
        )

    @property
    def denial_observed(self) -> bool:
        if self.after is None:
            return False
        observation = self.after.observation
        if (
            observation.http_status != 200
            or observation.terminal not in {Terminal.ANSWERED, Terminal.ABSTAINED}
            or observation.scope_hash is None
            or self.after.error_code is not None
        ):
            return False
        forbidden = set(self.target.chunk_ids)
        for boundary in (Boundary.RETRIEVAL_ACCEPTED, Boundary.CONTEXT):
            stage = _stage(self.after, boundary)
            if (
                stage is None
                or stage.state != "observed"
                or stage.truncated
                or forbidden.intersection(stage.chunk_ids)
            ):
                return False
        delivered = _stage(self.after, Boundary.CITATIONS_DELIVERED)
        if (
            delivered is None
            or delivered.state not in {"observed", "not_reached"}
            or delivered.truncated
            or tuple(citation.chunk_id for citation in self.after.citations)
            != delivered.chunk_ids
            or any(
                citation.chunk_id in forbidden
                or citation.document_id == self.target.document_id
                or citation.version_id == self.target.version_id
                for citation in self.after.citations
            )
        ):
            return False
        if observation.terminal == Terminal.ANSWERED:
            if delivered.state != "observed":
                return False
            for boundary in (Boundary.OUTPUT_CANDIDATE, Boundary.OUTPUT_DELIVERED):
                stage = _stage(self.after, boundary)
                if stage is None or stage.state != "observed" or stage.truncated:
                    return False
        return True

    @property
    def coverage_complete(self) -> bool:
        return (
            self.positive_before
            and not self.runtime_failed
            and self.update_committed
            and self.denial_observed
            and self.elapsed_ms is not None
            and self.grant_restore == "restored"
        )

    @property
    def revocation_to_confirmation_ms(self) -> float | None:
        return self.elapsed_ms if self.coverage_complete else None


def revocation_measurement(
    target: UtilityDocumentBinding,
    before: UtilityQueryRecord,
    after: UtilityQueryRecord | None = None,
    *,
    update_committed: bool = False,
    elapsed_ms: float | None = None,
    grant_restore: GrantRestore = "not_needed",
    runtime_failed: bool = False,
) -> RevocationMeasurement:
    return RevocationMeasurement.model_validate(
        {
            "target": target.model_dump(),
            "before": before.model_dump(),
            "after": after.model_dump() if after is not None else None,
            "update_committed": update_committed,
            "elapsed_ms": elapsed_ms,
            "grant_restore": grant_restore,
            "runtime_failed": runtime_failed,
        }
    )


def _permissions(
    workspace: AuditWorkspace, target: UtilityDocumentBinding
) -> dict[str, object]:
    workspace.validate_owned()
    with workspace.admin_engine.connect() as connection:
        row = connection.execute(
            select(Document.visibility, Document.current_version_id).where(
                Document.id == target.document_id
            )
        ).one()
        if row.current_version_id != target.version_id:
            raise ValueError(_ERROR)
        grants = tuple(
            connection.execute(
                select(DocumentGrant.user_id, DocumentGrant.group_id).where(
                    DocumentGrant.document_id == target.document_id
                )
            )
        )
    return {
        "visibility": row.visibility,
        "user_ids": [
            str(grant.user_id) for grant in grants if grant.user_id is not None
        ],
        "group_ids": [
            str(grant.group_id) for grant in grants if grant.group_id is not None
        ],
    }


def capture_revocation(
    workspace: AuditWorkspace,
    corpus: UtilityCorpus,
    query: CorpusQuery,
    *,
    provider: GenerationProvider,
    store: QdrantChunkStore | None = None,
) -> RevocationMeasurement:
    corpus = _corpus(corpus)
    query = CorpusQuery.model_validate(query.model_dump())
    if query not in corpus.queries or len(query.relevant_document_ids) != 1:
        raise ValueError(_ERROR)
    logical_id = query.relevant_document_ids[0]
    if logical_id not in workspace.bindings.documents:
        raise ValueError(_ERROR)
    target = UtilityDocumentBinding(
        logical_id=logical_id, **workspace.bindings.documents[logical_id].model_dump()
    )
    before = capture_utility_query(
        workspace, corpus, query, provider=provider, store=store
    )
    initial = revocation_measurement(
        target,
        before.record,
        runtime_failed=before.interrupted
        or before.record.observation.terminal
        not in {Terminal.ANSWERED, Terminal.ABSTAINED},
    )
    if before.interrupted or not initial.positive_before:
        return initial
    after: UtilityQueryRecord | None = None
    committed = attempted = False
    elapsed: float | None = None
    restored: GrantRestore = "not_needed"
    original: dict[str, object] | None = None
    runtime_failed = False
    try:
        original = _permissions(workspace, target)
        actor = next(actor for actor in corpus.actors if actor.id == query.actor_id)
        organization = next(
            org for org in corpus.organizations if org.id == actor.organization_id
        )
        manager = next(
            actor for actor in corpus.actors if actor.id == f"{organization.id}-owner"
        )
        headers = login_actor(
            workspace, email=manager.email, organization_slug=organization.slug
        )
        attempted = True
        with workspace.mutation():
            response = workspace.client.patch(
                f"/api/v1/documents/{target.document_id}",
                headers=headers,
                json={"visibility": "restricted", "user_ids": [], "group_ids": []},
            )
            if response.status_code == 200:
                committed = True
                acknowledged = perf_counter()
        if committed:
            captured = capture_utility_query(
                workspace, corpus, query, provider=provider, store=store
            )
            after = captured.record
            runtime_failed = captured.interrupted or after.observation.terminal not in {
                Terminal.ANSWERED,
                Terminal.ABSTAINED,
            }
            elapsed = (perf_counter() - acknowledged) * 1000
        else:
            runtime_failed = True
    except (Exception, KeyboardInterrupt):
        runtime_failed = True
    finally:
        if attempted and original is not None:
            restored = "failed"
            try:
                with workspace.mutation():
                    response = workspace.client.patch(
                        f"/api/v1/documents/{target.document_id}",
                        headers=headers,
                        json=original,
                    )
                if response.status_code == 200:
                    restored = "restored"
                else:
                    runtime_failed = True
            except (Exception, KeyboardInterrupt):
                restored = "failed"
                runtime_failed = True
    return revocation_measurement(
        target,
        before.record,
        after,
        update_committed=committed,
        elapsed_ms=elapsed,
        grant_restore=restored,
        runtime_failed=runtime_failed,
    )
