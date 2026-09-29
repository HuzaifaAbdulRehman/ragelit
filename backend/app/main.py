from collections.abc import Callable, Mapping
from typing import cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router
from app.api.routes.health import DependencyCheck
from app.core.config import Settings


def create_app(
    settings: Settings | None = None,
    readiness_checks: Mapping[str, DependencyCheck] | None = None,
) -> FastAPI:
    load_settings = cast(Callable[[], Settings], Settings)
    resolved_settings = settings or load_settings()
    app = FastAPI(title="RAGelit API", version="0.1.0")
    app.state.settings = resolved_settings
    app.state.readiness_checks = dict(readiness_checks or {})

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
