from app.db.session import build_engine


def test_postgres_engine_adds_a_bounded_connect_timeout() -> None:
    engine = build_engine("postgresql+psycopg://app:test@db/ragelit")

    assert engine.url.query["connect_timeout"] == "2"
    engine.dispose()


def test_postgres_engine_preserves_an_explicit_connect_timeout() -> None:
    engine = build_engine("postgresql+psycopg://app:test@db/ragelit?connect_timeout=7")

    assert engine.url.query["connect_timeout"] == "7"
    engine.dispose()
