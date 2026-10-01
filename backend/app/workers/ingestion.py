import argparse
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from qdrant_client import QdrantClient
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.session import build_engine, build_session_factory
from app.documents.chunking import chunk_sections
from app.documents.extraction import DocumentError, extract_text
from app.documents.models import Document, DocumentGrant, DocumentVersion, IngestionJob
from app.documents.storage import storage_path
from app.retrieval.embeddings import EmbeddingProvider, FastEmbedProvider
from app.retrieval.store import IndexContext, QdrantChunkStore
from app.tenancy.models import Organization
from app.tenancy.rls import set_request_context


@dataclass(frozen=True, slots=True)
class JobClaim:
    id: UUID
    claim_id: UUID
    organization_id: UUID
    version_id: UUID
    document_id: UUID
    filename: str
    media_type: str
    storage_key: str


def _context(session: Session, organization_id: UUID) -> None:
    set_request_context(session, user_id=UUID(int=0), organization_id=organization_id)
    session.execute(
        select(Organization.id)
        .where(Organization.id == organization_id)
        .with_for_update()
    )


def claim_job(
    organization_id: UUID, *, session: Session, now: datetime | None = None
) -> JobClaim | None:
    _context(session, organization_id)
    current = now or datetime.now(UTC)
    job = session.scalar(
        select(IngestionJob)
        .where(
            IngestionJob.organization_id == organization_id,
            or_(
                IngestionJob.state == "queued",
                (IngestionJob.state == "processing")
                & (IngestionJob.lease_until < current),
            ),
        )
        .order_by(IngestionJob.created_at, IngestionJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        session.rollback()
        return None
    version = session.get(DocumentVersion, job.version_id)
    if version is None:
        raise DocumentError("resource_not_found", 404)
    document = session.get(Document, version.document_id)
    if document is None:
        raise DocumentError("resource_not_found", 404)
    if job.attempts >= 3 or document.state == "deleted":
        job.state, job.error_code = "failed", "retry_exhausted"
        if document.state != "deleted":
            document.state = version.state = "failed"
        session.commit()
        return None
    job.attempts += 1
    job.state, job.claim_id, job.error_code = "processing", uuid4(), None
    job.lease_until = current + timedelta(hours=1)
    document.state = version.state = "processing"
    claim = JobClaim(
        job.id,
        job.claim_id,
        organization_id,
        version.id,
        document.id,
        document.filename,
        document.media_type,
        version.storage_key,
    )
    session.commit()
    return claim


def _index_context(claim: JobClaim, session: Session) -> IndexContext:
    document = session.scalar(
        select(Document).where(Document.id == claim.document_id).with_for_update()
    )
    if document is None or document.state == "deleted":
        raise DocumentError("job_claim_lost", 409)
    grants = list(
        session.scalars(
            select(DocumentGrant).where(DocumentGrant.document_id == document.id)
        )
    )
    return IndexContext(
        claim.organization_id,
        document.id,
        claim.version_id,
        claim.claim_id,
        document.filename,
        document.visibility,
        tuple(grant.user_id for grant in grants if grant.user_id is not None),
        tuple(grant.group_id for grant in grants if grant.group_id is not None),
    )


def run_once(
    organization_id: UUID,
    *,
    factory: sessionmaker[Session],
    settings: Settings,
    store: QdrantChunkStore,
    embeddings: EmbeddingProvider,
) -> bool:
    with factory() as session:
        claim = claim_job(organization_id, session=session)
    if claim is None:
        return False
    try:
        path = storage_path(settings.data_dir, claim.storage_key)
        chunks = chunk_sections(extract_text(path, claim.media_type), claim.version_id)
        with factory() as session:
            _context(session, organization_id)
            context = _index_context(claim, session)
            session.rollback()
        store.stage(context, chunks, embeddings)
        with factory() as session:
            _context(session, organization_id)
            job = session.scalar(
                select(IngestionJob)
                .where(IngestionJob.id == claim.id)
                .with_for_update()
            )
            if (
                job is None
                or job.claim_id != claim.claim_id
                or job.state != "processing"
                or job.lease_until is None
                or job.lease_until <= datetime.now(UTC)
            ):
                raise DocumentError("job_claim_lost", 409)
            context = _index_context(claim, session)
            store.activate(context)
            version = session.get(DocumentVersion, claim.version_id)
            document = session.get(Document, claim.document_id)
            assert version is not None and document is not None
            version.state = document.state = job.state = "ready"
            session.execute(
                update(DocumentVersion)
                .where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.id != version.id,
                    DocumentVersion.state == "ready",
                )
                .values(state="superseded")
            )
            version.chunk_count = len(chunks)
            job.lease_until = None
            session.commit()
    except Exception as error:
        code = error.code if isinstance(error, DocumentError) else "ingestion_failed"
        with factory() as session:
            _context(session, organization_id)
            job = session.scalar(
                select(IngestionJob)
                .where(IngestionJob.id == claim.id)
                .with_for_update()
            )
            if (
                job is not None
                and job.claim_id == claim.claim_id
                and job.state == "processing"
            ):
                job.state, job.error_code, job.lease_until = "failed", code, None
                version = session.get(DocumentVersion, claim.version_id)
                document = session.get(Document, claim.document_id)
                if (
                    version is not None
                    and document is not None
                    and document.state != "deleted"
                ):
                    version.state = document.state = "failed"
                session.commit()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process documents for one organization."
    )
    parser.add_argument("--organization-id", type=UUID, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = Settings()  # type: ignore[call-arg]
    provider = FastEmbedProvider(cache_dir=str(settings.data_dir / "models"))
    client = QdrantClient(url=str(settings.qdrant_url), timeout=10)
    store = QdrantChunkStore(
        client, settings.qdrant_collection, dimension=provider.dimension
    )
    store.ensure_collection()
    engine = build_engine(settings.database_url)
    try:
        factory = build_session_factory(engine)
        while True:
            processed = run_once(
                args.organization_id,
                factory=factory,
                settings=settings,
                store=store,
                embeddings=provider,
            )
            if args.once:
                break
            if not processed:
                time.sleep(2)
    finally:
        client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
