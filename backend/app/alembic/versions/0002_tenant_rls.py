"""Force tenant isolation for application queries."""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_tenant_rls"
down_revision: str | None = "0001_identity_tenancy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("memberships", "groups", "group_members", "refresh_sessions")
ORGANIZATION_MATCH = """
organization_id = NULLIF(
    current_setting('app.organization_id', true),
    ''
)::uuid
"""
USER_MATCH = """
user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
"""
REFRESH_TOKEN_MATCH = """
token_hash = NULLIF(current_setting('app.refresh_token_hash', true), '')
"""


def _create_organization_policies(table: str) -> None:
    op.execute(
        f'CREATE POLICY "{table}_select" ON "{table}" '
        f"FOR SELECT USING ({ORGANIZATION_MATCH})"
    )
    op.execute(
        f'CREATE POLICY "{table}_insert" ON "{table}" '
        f"FOR INSERT WITH CHECK ({ORGANIZATION_MATCH})"
    )
    op.execute(
        f'CREATE POLICY "{table}_update" ON "{table}" '
        f"FOR UPDATE USING ({ORGANIZATION_MATCH}) "
        f"WITH CHECK ({ORGANIZATION_MATCH})"
    )
    op.execute(
        f'CREATE POLICY "{table}_delete" ON "{table}" '
        f"FOR DELETE USING ({ORGANIZATION_MATCH})"
    )


def upgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')

    op.execute(
        'CREATE POLICY "memberships_select" ON "memberships" '
        "FOR SELECT USING ("
        f"({ORGANIZATION_MATCH}) OR (({USER_MATCH}) AND is_active)"
        ")"
    )
    op.execute(
        'CREATE POLICY "memberships_insert" ON "memberships" '
        f"FOR INSERT WITH CHECK ({ORGANIZATION_MATCH})"
    )
    op.execute(
        'CREATE POLICY "memberships_update" ON "memberships" '
        f"FOR UPDATE USING ({ORGANIZATION_MATCH}) "
        f"WITH CHECK ({ORGANIZATION_MATCH})"
    )
    op.execute(
        'CREATE POLICY "memberships_delete" ON "memberships" '
        f"FOR DELETE USING ({ORGANIZATION_MATCH})"
    )
    for table in TENANT_TABLES[1:3]:
        _create_organization_policies(table)
    op.execute(
        'CREATE POLICY "refresh_sessions_select" ON "refresh_sessions" '
        "FOR SELECT USING ("
        f"({ORGANIZATION_MATCH}) OR ({REFRESH_TOKEN_MATCH})"
        ")"
    )
    op.execute(
        'CREATE POLICY "refresh_sessions_insert" ON "refresh_sessions" '
        f"FOR INSERT WITH CHECK ({ORGANIZATION_MATCH})"
    )
    op.execute(
        'CREATE POLICY "refresh_sessions_update" ON "refresh_sessions" '
        "FOR UPDATE USING ("
        f"({ORGANIZATION_MATCH}) OR ({REFRESH_TOKEN_MATCH})"
        ") WITH CHECK ("
        f"({ORGANIZATION_MATCH}) OR ({REFRESH_TOKEN_MATCH})"
        ")"
    )
    op.execute(
        'CREATE POLICY "refresh_sessions_delete" ON "refresh_sessions" '
        f"FOR DELETE USING ({ORGANIZATION_MATCH})"
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ragelit_app'
            ) THEN
                GRANT USAGE ON SCHEMA public TO ragelit_app;
                GRANT SELECT, INSERT, UPDATE
                    ON TABLE users
                    TO ragelit_app;
                GRANT SELECT, UPDATE
                    ON TABLE organizations
                    TO ragelit_app;
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON TABLE memberships, groups, group_members, refresh_sessions
                    TO ragelit_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ragelit_app'
            ) THEN
                REVOKE SELECT, INSERT, UPDATE
                    ON TABLE users
                    FROM ragelit_app;
                REVOKE SELECT, UPDATE
                    ON TABLE organizations
                    FROM ragelit_app;
                REVOKE SELECT, INSERT, UPDATE, DELETE
                    ON TABLE memberships, groups, group_members, refresh_sessions
                    FROM ragelit_app;
                REVOKE USAGE ON SCHEMA public FROM ragelit_app;
            END IF;
        END
        $$
        """
    )
    for table in reversed(TENANT_TABLES):
        for operation in ("delete", "update", "insert", "select"):
            op.execute(f'DROP POLICY "{table}_{operation}" ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
