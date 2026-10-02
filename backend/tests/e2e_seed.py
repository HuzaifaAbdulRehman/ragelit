from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import hash_password
from app.identity.models import User
from app.seed import seed_demo
from app.tenancy.enums import Role
from app.tenancy.models import Group, GroupMember, Membership, Organization
from tests.e2e_app import fixture_store, validate_fixture_settings

DATABASE_NAME = "ragelit_e2e"
SERVER_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres"
ADMIN_URL = f"postgresql+psycopg://postgres:postgres@127.0.0.1:5432/{DATABASE_NAME}"


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    validate_fixture_settings(settings)
    server_engine = create_engine(SERVER_URL, isolation_level="AUTOCOMMIT")
    with server_engine.connect() as connection:
        connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT FROM pg_catalog.pg_roles
                        WHERE rolname = 'ragelit_app'
                    ) THEN
                        CREATE ROLE ragelit_app LOGIN PASSWORD 'ragelit_app'
                            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
                            NOBYPASSRLS;
                    END IF;
                END
                $$
                """
            )
        )
        connection.execute(text("ALTER ROLE ragelit_app PASSWORD 'ragelit_app' LOGIN"))
        connection.execute(
            text(f'DROP DATABASE IF EXISTS "{DATABASE_NAME}" WITH (FORCE)')
        )
        connection.execute(text(f'CREATE DATABASE "{DATABASE_NAME}"'))
    server_engine.dispose()

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", ADMIN_URL)
    command.upgrade(config, "head")
    engine = create_engine(ADMIN_URL)
    with Session(engine) as session:
        seed_demo(
            session,
            password_factory=lambda: "task-nine-test-password",
        )
        for email, slug, role, group_name in (
            ("research@northstar.example", "northstar-labs", Role.MEMBER, "Research"),
            ("owner@harbor.example", "harbor-works", Role.OWNER, "Finance"),
        ):
            organization = session.scalar(
                select(Organization).where(Organization.slug == slug)
            )
            assert organization is not None
            user = User(
                email=email,
                password_hash=hash_password("task-nine-test-password"),
            )
            session.add(user)
            session.flush()
            session.add(
                Membership(user_id=user.id, organization_id=organization.id, role=role)
            )
            group = session.scalar(
                select(Group).where(
                    Group.organization_id == organization.id, Group.name == group_name
                )
            )
            assert group is not None
            session.add(
                GroupMember(
                    user_id=user.id, organization_id=organization.id, group_id=group.id
                )
            )
        session.commit()
    engine.dispose()
    store = fixture_store(settings)
    try:
        if store.client.collection_exists("ragelit_e2e"):
            store.client.delete_collection("ragelit_e2e")
        store.ensure_collection()
    finally:
        store.client.close()


if __name__ == "__main__":
    main()
