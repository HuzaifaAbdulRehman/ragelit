from uuid import UUID

from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.documents.extraction import DocumentError
from app.documents.models import Document, DocumentGrant, DocumentVersion, IngestionJob
from app.documents.schemas import AccessCommand
from app.documents.storage import StoredUpload
from app.retrieval.store import QdrantChunkStore
from app.tenancy.models import Group, Membership, Organization
from app.tenancy.scope import RequestPrincipal


def locked_document(
    principal: RequestPrincipal, document_id: UUID, session: Session
) -> Document:
    session.execute(
        select(Organization.id)
        .where(Organization.id == principal.organization_id)
        .with_for_update()
    )
    document = session.scalar(
        select(Document)
        .where(
            Document.id == document_id,
            Document.organization_id == principal.organization_id,
            Document.state != "deleted",
        )
        .with_for_update()
    )
    if document is None:
        raise DocumentError("resource_not_found", 404)
    return document


def change_access(
    principal: RequestPrincipal,
    document_id: UUID,
    command: AccessCommand,
    *,
    session: Session,
    store: QdrantChunkStore,
) -> Document:
    document = locked_document(principal, document_id, session)
    users, groups = set(command.user_ids), set(command.group_ids)
    valid_users = set(
        session.scalars(
            select(Membership.user_id).where(
                Membership.organization_id == principal.organization_id,
                Membership.is_active.is_(True),
                Membership.user_id.in_(users),
            )
        )
    )
    valid_groups = set(
        session.scalars(
            select(Group.id).where(
                Group.organization_id == principal.organization_id, Group.id.in_(groups)
            )
        )
    )
    if users != valid_users or groups != valid_groups:
        raise DocumentError("resource_not_found", 404)
    if document.state == "ready":
        try:
            store.set_access(
                principal.organization_id,
                document.id,
                command.visibility,
                tuple(users),
                tuple(groups),
            )
        except (UnexpectedResponse, ResponseHandlingException, OSError) as error:
            _queue_projection_repair(document, session)
            raise DocumentError("projection_unavailable", 503) from error
    session.execute(
        delete(DocumentGrant).where(DocumentGrant.document_id == document.id)
    )
    for user_id in users:
        session.add(
            DocumentGrant(
                organization_id=principal.organization_id,
                document_id=document.id,
                user_id=user_id,
            )
        )
    for group_id in groups:
        session.add(
            DocumentGrant(
                organization_id=principal.organization_id,
                document_id=document.id,
                group_id=group_id,
            )
        )
    document.visibility = command.visibility
    session.commit()
    return document


def delete_document(
    principal: RequestPrincipal,
    document_id: UUID,
    *,
    session: Session,
    store: QdrantChunkStore,
) -> None:
    document = locked_document(principal, document_id, session)
    if document.state == "ready":
        try:
            store.deactivate(principal.organization_id, document.id)
        except (UnexpectedResponse, ResponseHandlingException, OSError) as error:
            _queue_projection_repair(document, session)
            raise DocumentError("projection_unavailable", 503) from error
    versions = list(
        session.scalars(
            select(DocumentVersion).where(DocumentVersion.document_id == document.id)
        )
    )
    for version in versions:
        version.state = "deleted"
    jobs = session.scalars(
        select(IngestionJob).where(
            IngestionJob.version_id.in_([version.id for version in versions])
        )
    )
    for job in jobs:
        job.state, job.error_code, job.lease_until = "failed", "document_deleted", None
        job.claim_id = None
    document.state = "deleted"
    session.commit()


def _queue_projection_repair(document: Document, session: Session) -> None:
    version = session.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document.id, DocumentVersion.state == "ready"
        )
        .order_by(DocumentVersion.created_at.desc())
        .limit(1)
    )
    if version is None:
        return
    job = session.scalar(
        select(IngestionJob).where(IngestionJob.version_id == version.id)
    )
    if job is None:
        job = IngestionJob(
            organization_id=document.organization_id, version_id=version.id
        )
        session.add(job)
    job.state, job.error_code, job.attempts = "queued", "projection_reconciliation", 0
    job.claim_id, job.lease_until = None, None
    session.commit()


def retry_document(
    principal: RequestPrincipal, document_id: UUID, *, session: Session
) -> Document:
    document = locked_document(principal, document_id, session)
    if document.state != "failed":
        raise DocumentError("document_not_retryable", 409)
    row = session.execute(
        select(DocumentVersion, IngestionJob)
        .join(IngestionJob, IngestionJob.version_id == DocumentVersion.id)
        .where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.state == "failed",
        )
        .order_by(DocumentVersion.created_at.desc())
        .limit(1)
    ).one_or_none()
    if row is None:
        raise DocumentError("document_not_retryable", 409)
    version, job = row
    document.state = version.state = job.state = "queued"
    job.error_code, job.claim_id, job.lease_until, job.attempts = None, None, None, 0
    session.commit()
    return document


def replace_document(
    principal: RequestPrincipal,
    document_id: UUID,
    filename: str,
    media_type: str,
    version_id: UUID,
    upload: StoredUpload,
    *,
    session: Session,
) -> tuple[Document, bool]:
    document = locked_document(principal, document_id, session)
    if document.state not in ("ready", "failed"):
        raise DocumentError("document_processing", 409)
    duplicate = session.scalar(
        select(Document.id).where(
            Document.organization_id == principal.organization_id,
            Document.checksum == upload.checksum,
            Document.id != document.id,
        )
    )
    if duplicate is not None:
        raise DocumentError("duplicate_document", 409)
    if document.checksum == upload.checksum:
        return document, False
    obsolete_jobs = session.scalars(
        select(IngestionJob)
        .join(DocumentVersion, DocumentVersion.id == IngestionJob.version_id)
        .where(
            DocumentVersion.document_id == document.id,
            IngestionJob.state.in_(("queued", "processing")),
        )
    )
    for job in obsolete_jobs:
        job.state, job.error_code = "failed", "version_replaced"
        job.claim_id, job.lease_until = None, None
    document.checksum, document.filename, document.media_type, document.state = (
        upload.checksum,
        filename,
        media_type,
        "queued",
    )
    session.add(
        DocumentVersion(
            id=version_id,
            organization_id=principal.organization_id,
            document_id=document.id,
            storage_key=upload.storage_key,
            checksum=upload.checksum,
            byte_count=upload.byte_count,
        )
    )
    session.flush()
    document.current_version_id = version_id
    session.add(
        IngestionJob(organization_id=principal.organization_id, version_id=version_id)
    )
    session.commit()
    return document, True
