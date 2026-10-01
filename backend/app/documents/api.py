from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentPrincipal, DatabaseSession, require_action
from app.documents.extraction import DocumentError
from app.documents.schemas import DocumentResponse
from app.documents.service import create_document, list_documents, upload_media_type
from app.documents.storage import stream_upload
from app.tenancy.policy import Action
from app.tenancy.scope import RequestPrincipal

router = APIRouter(prefix="/documents", tags=["documents"])
DocumentManager = Annotated[
    RequestPrincipal, Depends(require_action(Action.DOCUMENTS_MANAGE))
]


@router.post("", status_code=202, response_model=DocumentResponse)
async def upload_document(
    request: Request,
    principal: DocumentManager,
    session: DatabaseSession,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> DocumentResponse:
    media = upload_media_type(filename, request.headers.get("content-type", ""))
    settings = request.app.state.settings
    version_id = uuid4()
    upload = await stream_upload(
        request.stream(),
        root=settings.data_dir,
        organization_id=principal.organization_id,
        version_id=version_id,
        max_bytes=settings.max_upload_bytes,
    )
    keep = False
    try:
        document, keep = await run_in_threadpool(
            create_document,
            principal,
            filename,
            media,
            version_id,
            upload,
            session=session,
        )
        return DocumentResponse.model_validate(document)
    finally:
        if not keep:
            upload.path.unlink(missing_ok=True)


@router.get("", response_model=list[DocumentResponse])
def documents(
    principal: CurrentPrincipal,
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DocumentResponse]:
    return [
        DocumentResponse.model_validate(document)
        for document in list_documents(
            principal, session=session, limit=limit, offset=offset
        )
    ]


@router.get("/{document_id}", response_model=DocumentResponse)
def document_detail(
    document_id: UUID, principal: CurrentPrincipal, session: DatabaseSession
) -> DocumentResponse:
    documents = list_documents(principal, session=session, document_id=document_id)
    if not documents:
        raise DocumentError("resource_not_found", 404)
    return DocumentResponse.model_validate(documents[0])
