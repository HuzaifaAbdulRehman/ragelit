from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def set_request_context(
    session: Session,
    *,
    user_id: UUID,
    organization_id: UUID,
) -> None:
    session.execute(
        text(
            """
            SELECT
                set_config('app.user_id', :user_id, true),
                set_config('app.organization_id', :organization_id, true)
            """
        ),
        {
            "user_id": str(user_id),
            "organization_id": str(organization_id),
        },
    )


def set_refresh_token_context(session: Session, *, token_hash: str) -> None:
    session.execute(
        text("SELECT set_config('app.refresh_token_hash', :token_hash, true)"),
        {"token_hash": token_hash},
    )
