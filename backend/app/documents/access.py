from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.documents.extraction import DocumentError
from app.documents.models import Document, DocumentGrant
from app.documents.schemas import AccessResponse
from app.tenancy.scope import RequestPrincipal


def read_document_access(
    principal: RequestPrincipal, document_id: UUID, *, session: Session
) -> AccessResponse:
    document = session.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.organization_id == principal.organization_id,
            Document.state != "deleted",
        )
    )
    if document is None:
        raise DocumentError("resource_not_found", 404)
    grants = list(
        session.scalars(
            select(DocumentGrant).where(
                DocumentGrant.document_id == document_id,
                DocumentGrant.organization_id == principal.organization_id,
            )
        )
    )
    return AccessResponse(
        visibility=document.visibility,
        user_ids=sorted(
            {grant.user_id for grant in grants if grant.user_id is not None}
        ),
        group_ids=sorted(
            {grant.group_id for grant in grants if grant.group_id is not None}
        ),
    )
