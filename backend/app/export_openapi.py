"""Regenerate the API contract without running services or loading credentials."""

import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.main import create_app


def main() -> None:
    settings = Settings.model_validate(
        {
            "environment": "test",
            "secret_key": "contract-export-has-no-live-credentials",
            "database_admin_url": "unused",
            "database_url": "unused",
            "qdrant_url": "http://localhost:6333",
        }
    )
    engine = create_engine("sqlite://")
    try:
        schema = create_app(
            settings, session_factory=sessionmaker(bind=engine)
        ).openapi()
        target = Path(__file__).resolve().parents[1] / "openapi.json"
        target.write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
