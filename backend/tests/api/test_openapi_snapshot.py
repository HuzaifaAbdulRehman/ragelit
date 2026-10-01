import json
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.main import create_app


def _openapi_schema(settings: Settings) -> dict[str, Any]:
    session_factory = sessionmaker(bind=create_engine("sqlite://"))
    return create_app(settings, session_factory=session_factory).openapi()


def test_openapi_snapshot_is_current(tenant_settings: Settings) -> None:
    actual = _openapi_schema(tenant_settings)
    snapshot_path = Path(__file__).resolve().parents[2] / "openapi.json"
    expected = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert actual == expected


def test_auth_success_responses_have_a_generated_schema(
    tenant_settings: Settings,
) -> None:
    schema = _openapi_schema(tenant_settings)
    expected_response = {
        "$ref": "#/components/schemas/AccessTokenResponse",
    }

    for path in (
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/switch-organization",
    ):
        response_schema = schema["paths"][path]["post"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert response_schema == expected_response


def test_protected_routes_document_bearer_auth_and_problem_responses(
    tenant_settings: Settings,
) -> None:
    schema = _openapi_schema(tenant_settings)

    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "scheme": "bearer",
        "type": "http",
    }
    member_role = schema["paths"][
        "/api/v1/organizations/{organization_id}/members/{membership_id}/role"
    ]["patch"]
    assert member_role["security"] == [{"HTTPBearer": []}]
    assert {"401", "403", "404", "409"}.issubset(member_role["responses"])
    assert (
        member_role["responses"]["404"]["content"]["application/problem+json"][
            "schema"
        ]["$ref"]
        == "#/components/schemas/ProblemDetail"
    )
    assert "401" in schema["paths"]["/api/v1/auth/login"]["post"]["responses"]


def test_validation_responses_document_problem_details(
    tenant_settings: Settings,
) -> None:
    schema = _openapi_schema(tenant_settings)
    response = schema["paths"]["/api/v1/auth/login"]["post"]["responses"]["422"]

    assert set(response["content"]) == {"application/problem+json"}
    assert (
        response["content"]["application/problem+json"]["schema"]["$ref"]
        == "#/components/schemas/ProblemDetail"
    )
