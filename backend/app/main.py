from collections.abc import Callable, Mapping
from typing import cast
from urllib.request import urlopen

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.api.router import router
from app.api.routes.health import DependencyCheck
from app.core.config import Settings
from app.core.problems import (
    ProblemException,
    problem_exception_handler,
    request_validation_exception_handler,
)
from app.db.session import build_engine, build_session_factory


def _database_ready(factory: sessionmaker[Session]) -> bool:
    with factory() as session:
        return session.execute(text("SELECT 1")).scalar_one() == 1


def _qdrant_ready(qdrant_url: object) -> bool:
    endpoint = f"{str(qdrant_url).rstrip('/')}/readyz"
    with urlopen(endpoint, timeout=2) as response:
        return int(response.status) == 200


def create_app(
    settings: Settings | None = None,
    readiness_checks: Mapping[str, DependencyCheck] | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    load_settings = cast(Callable[[], Settings], Settings)
    resolved_settings = settings or load_settings()
    app = FastAPI(
        title="RAGelit API",
        version="0.1.0",
        responses={
            422: {
                "description": "Unprocessable Content",
                "content": {
                    "application/problem+json": {
                        "schema": {"$ref": "#/components/schemas/ProblemDetail"}
                    }
                },
            }
        },
    )
    app.state.settings = resolved_settings
    resolved_factory = session_factory or build_session_factory(
        build_engine(resolved_settings.database_url)
    )
    app.state.session_factory = resolved_factory
    app.state.readiness_checks = (
        dict(readiness_checks)
        if readiness_checks is not None
        else {
            "postgres": lambda: _database_ready(resolved_factory),
            "qdrant": lambda: _qdrant_ready(resolved_settings.qdrant_url),
        }
    )
    app.add_exception_handler(ProblemException, problem_exception_handler)
    app.add_exception_handler(
        RequestValidationError,
        request_validation_exception_handler,
    )

    if resolved_settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_settings.allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(router)
    return app
