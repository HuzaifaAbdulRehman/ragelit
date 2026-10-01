from pathlib import PurePath
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.documents.extraction import DOCX, DocumentError
from app.documents.models import Document, DocumentGrant, DocumentVersion, IngestionJob
from app.documents.storage import StoredUpload
from app.tenancy.models import Organization
from app.tenancy.policy import Action, role_allows
from app.tenancy.scope import RequestPrincipal, load_access_scope


def upload_media_type(filename: str, content_type: str) -> str:
    if (
        not filename
        or len(filename) > 255
        or any(ord(char) < 32 for char in filename)
        or any(char in filename for char in "/\\:")
    ):
        raise DocumentError("invalid_filename")
    extensions = {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".docx": DOCX,
        ".pdf": "application/pdf",
    }
    media = extensions.get(PurePath(filename).suffix.lower())
    incoming = content_type.split(";", 1)[0].strip().lower()
    if (
        media is None
        or incoming not in (media, "application/octet-stream")
        and not (media == "text/markdown" and incoming == "text/plain")
    ):
        raise DocumentError("unsupported_document_type", 415)
    return media


def create_document(
    principal: RequestPrincipal,
    filename: str,
    media_type: str,
    version_id: UUID,
    upload: StoredUpload,
    *,
    session: Session,
) -> tuple[Document, bool]:
    session.execute(
        select(Organization.id)
        .where(Organization.id == principal.organization_id)
        .with_for_update()
    )
    existing = session.scalar(
        select(Document).where(
            Document.organization_id == principal.organization_id,
            Document.checksum == upload.checksum,
        )
    )
    if existing is not None:
        if existing.state == "deleted":
            raise DocumentError("document_deleted", 409)
        return existing, False
    document = Document(
        organization_id=principal.organization_id,
        filename=filename,
        media_type=media_type,
        checksum=upload.checksum,
    )
    session.add(document)
    session.flush()
    version = DocumentVersion(
        id=version_id,
        organization_id=principal.organization_id,
        document_id=document.id,
        storage_key=upload.storage_key,
        checksum=upload.checksum,
        byte_count=upload.byte_count,
    )
    session.add(version)
    session.flush()
    document.current_version_id = version_id
    session.add(
        IngestionJob(organization_id=principal.organization_id, version_id=version_id)
    )
    session.commit()
    return document, True


def list_documents(
    principal: RequestPrincipal,
    *,
    session: Session,
    limit: int = 50,
    offset: int = 0,
    document_id: UUID | None = None,
) -> list[Document]:
    query = select(Document).where(
        Document.organization_id == principal.organization_id,
        Document.state != "deleted",
    )
    if not role_allows(principal.role, Action.DOCUMENTS_MANAGE):
        scope = load_access_scope(principal, session=session)
        granted = exists(
            select(DocumentGrant.id).where(
                DocumentGrant.document_id == Document.id,
                DocumentGrant.organization_id == scope.organization_id,
                or_(
                    DocumentGrant.user_id == scope.user_id,
                    DocumentGrant.group_id.in_(scope.group_ids),
                ),
            )
        )
        query = query.where(or_(Document.visibility == "organization", granted))
    if document_id is not None:
        query = query.where(Document.id == document_id)
    return list(
        session.scalars(
            query.order_by(Document.created_at, Document.id).limit(limit).offset(offset)
        )
    )
