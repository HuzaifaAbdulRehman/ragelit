import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import build_engine, build_session_factory
from app.tenancy.models import Organization
from app.workers.ingestion import run_once
from tests.e2e_app import FixtureEmbeddings, fixture_store, validate_fixture_settings


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    validate_fixture_settings(settings)
    admin = build_engine(settings.database_admin_url)
    try:
        with Session(admin) as session:
            organizations = list(
                session.scalars(
                    select(Organization.id).where(
                        Organization.slug.in_(("northstar-labs", "harbor-works"))
                    )
                )
            )
    finally:
        admin.dispose()
    if len(organizations) != 2:
        raise RuntimeError("E2E worker requires both seeded organizations")
    engine = build_engine(settings.database_url)
    store = fixture_store(settings)
    provider = FixtureEmbeddings()
    try:
        factory = build_session_factory(engine)
        while True:
            for organization_id in organizations:
                run_once(
                    organization_id,
                    factory=factory,
                    settings=settings,
                    store=store,
                    embeddings=provider,
                )
            time.sleep(0.2)
    finally:
        store.client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
