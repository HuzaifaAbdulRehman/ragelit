from datetime import UTC
from uuid import UUID

from sqlalchemy import UniqueConstraint

from app.db.base import Base, utc_now
from app.identity.models import RefreshSession, User
from app.tenancy.enums import Role
from app.tenancy.models import Group, GroupMember, Membership, Organization


def _unique_columns(table_name: str) -> set[tuple[str, ...]]:
    table = Base.metadata.tables[table_name]
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_role_values_are_persisted_contract() -> None:
    assert [role.value for role in Role] == ["owner", "admin", "auditor", "member"]


def test_email_is_normalized_before_persistence() -> None:
    user = User(email="  Person@Example.COM  ", password_hash="encoded")

    assert user.email == "person@example.com"


def test_unique_tenant_constraints_are_declared() -> None:
    assert ("email",) in _unique_columns("users")
    assert ("slug",) in _unique_columns("organizations")
    assert ("user_id", "organization_id") in _unique_columns("memberships")
    assert ("organization_id", "name") in _unique_columns("groups")
    assert ("group_id", "user_id") in _unique_columns("group_members")
    assert ("token_hash",) in _unique_columns("refresh_sessions")


def test_all_models_use_uuid_primary_keys() -> None:
    models = [User, RefreshSession, Organization, Membership, Group, GroupMember]

    for model in models:
        identifier = model.__table__.c.id
        assert identifier.primary_key
        assert identifier.type.python_type is UUID


def test_tenant_owned_rows_carry_organization_id() -> None:
    models = [RefreshSession, Membership, Group, GroupMember]

    for model in models:
        assert "organization_id" in model.__table__.c
        assert not model.__table__.c.organization_id.nullable


def test_utc_clock_is_timezone_aware() -> None:
    assert utc_now().tzinfo is UTC
