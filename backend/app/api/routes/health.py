import inspect
from collections.abc import Awaitable, Callable, Mapping

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.problems import problem_response

router = APIRouter(prefix="/health", tags=["health"])

DependencyCheck = Callable[[], bool | Awaitable[bool]]


async def _run_check(check: DependencyCheck) -> bool:
    try:
        result = check()
        if inspect.isawaitable(result):
            result = await result
        return result is True
    except Exception:
        return False


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", response_model=None)
async def readiness(request: Request) -> dict[str, object] | JSONResponse:
    checks: Mapping[str, DependencyCheck] = request.app.state.readiness_checks
    if not checks:
        return problem_response(
            status=503,
            code="readiness_not_configured",
            title="Service unavailable",
            detail="Readiness checks are not configured.",
        )

    dependencies = {
        name: "ok" if await _run_check(check) else "unavailable"
        for name, check in checks.items()
    }

    if "unavailable" in dependencies.values():
        return problem_response(
            status=503,
            code="dependency_unavailable",
            title="Service unavailable",
            detail="One or more required services are unavailable.",
            extensions={"dependencies": dependencies},
        )

    return {"status": "ok", "dependencies": dependencies}
