from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.seed import seed_demo

DATABASE_NAME = "ragelit_e2e"
SERVER_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres"
ADMIN_URL = f"postgresql+psycopg://postgres:postgres@127.0.0.1:5432/{DATABASE_NAME}"


def main() -> None:
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
        session.commit()
    engine.dispose()


if __name__ == "__main__":
    main()
