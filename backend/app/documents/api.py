from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentPrincipal, DatabaseSession, require_action
from app.documents.extraction import DocumentError
from app.documents.lifecycle import (
    change_access,
    delete_document,
    replace_document,
    retry_document,
)
from app.documents.models import DocumentVersion
from app.documents.schemas import AccessCommand, DocumentResponse, VersionResponse
from app.documents.service import create_document, list_documents, upload_media_type
from app.documents.storage import stream_upload
from app.retrieval.deps import ChunkStore
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


@router.patch("/{document_id}", response_model=DocumentResponse)
def update_access(
    document_id: UUID,
    command: AccessCommand,
    principal: DocumentManager,
    session: DatabaseSession,
    store: ChunkStore,
) -> DocumentResponse:
    return DocumentResponse.model_validate(
        change_access(principal, document_id, command, session=session, store=store)
    )


@router.delete("/{document_id}", status_code=204)
def remove_document(
    document_id: UUID,
    principal: DocumentManager,
    session: DatabaseSession,
    store: ChunkStore,
) -> Response:
    delete_document(principal, document_id, session=session, store=store)
    return Response(status_code=204)


@router.post("/{document_id}/retry", status_code=202, response_model=DocumentResponse)
def retry(
    document_id: UUID, principal: DocumentManager, session: DatabaseSession
) -> DocumentResponse:
    return DocumentResponse.model_validate(
        retry_document(principal, document_id, session=session)
    )


@router.get("/{document_id}/versions", response_model=list[VersionResponse])
def document_versions(
    document_id: UUID, principal: CurrentPrincipal, session: DatabaseSession
) -> list[VersionResponse]:
    if not list_documents(principal, session=session, document_id=document_id):
        raise DocumentError("resource_not_found", 404)
    versions = session.scalars(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.organization_id == principal.organization_id,
        )
        .order_by(DocumentVersion.created_at, DocumentVersion.id)
    )
    return [VersionResponse.model_validate(version) for version in versions]


@router.post(
    "/{document_id}/versions", status_code=202, response_model=DocumentResponse
)
async def replace_upload(
    document_id: UUID,
    request: Request,
    principal: DocumentManager,
    session: DatabaseSession,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> DocumentResponse:
    if not await run_in_threadpool(
        list_documents, principal, session=session, document_id=document_id
    ):
        raise DocumentError("resource_not_found", 404)
    media = upload_media_type(filename, request.headers.get("content-type", ""))
    settings, version_id = request.app.state.settings, uuid4()
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
            replace_document,
            principal,
            document_id,
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
