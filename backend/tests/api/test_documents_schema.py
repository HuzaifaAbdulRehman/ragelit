from uuid import uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from app.documents.models import Document, DocumentGrant, DocumentVersion, IngestionJob
from app.tenancy.rls import set_request_context
from tests.api.tenant_support import TenantApiSeed


def test_document_rows_fail_closed_without_context(
    tenant_database_engines: tuple[Engine, Engine], tenant_seed: TenantApiSeed
) -> None:
    admin, runtime = tenant_database_engines
    with Session(admin) as session:
        session.add(
            Document(
                organization_id=tenant_seed.organization_a_id,
                filename="secret.txt",
                media_type="text/plain",
                checksum=uuid4().hex * 2,
            )
        )
        session.commit()
    with Session(runtime) as session:
        assert list(session.scalars(select(Document))) == []
        for model in (Document, DocumentVersion, DocumentGrant, IngestionJob):
            assert list(session.scalars(select(model))) == []


def test_documents_are_forced_rls_tables(
    tenant_database_engines: tuple[Engine, Engine],
) -> None:
    admin, _ = tenant_database_engines
    with admin.connect() as connection:
        for name in (
            "documents",
            "document_versions",
            "document_grants",
            "ingestion_jobs",
        ):
            row = connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = :name"
                ),
                {"name": name},
            ).one()
            assert row == (True, True)


def test_version_cannot_reference_another_tenants_document(
    tenant_database_engines: tuple[Engine, Engine], tenant_seed: TenantApiSeed
) -> None:
    admin, _ = tenant_database_engines
    with Session(admin) as session:
        document = Document(
            organization_id=tenant_seed.organization_a_id,
            filename="a.txt",
            media_type="text/plain",
            checksum=uuid4().hex * 2,
        )
        session.add(document)
        session.flush()
        session.add(
            DocumentVersion(
                organization_id=tenant_seed.organization_b_id,
                document_id=document.id,
                storage_key="synthetic",
                checksum=document.checksum,
                byte_count=1,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_runtime_cannot_insert_document_for_another_organization(
    tenant_database_engines: tuple[Engine, Engine], tenant_seed: TenantApiSeed
) -> None:
    _, runtime = tenant_database_engines
    with Session(runtime) as session:
        set_request_context(
            session, user_id=uuid4(), organization_id=tenant_seed.organization_a_id
        )
        session.add(
            Document(
                organization_id=tenant_seed.organization_b_id,
                filename="b.txt",
                media_type="text/plain",
                checksum=uuid4().hex * 2,
            )
        )
        with pytest.raises(ProgrammingError, match="row-level security"):
            session.flush()
