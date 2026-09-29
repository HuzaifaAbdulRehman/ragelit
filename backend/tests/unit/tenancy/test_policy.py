import pytest

from app.tenancy.enums import Role
from app.tenancy.policy import Action, role_allows


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (Role.OWNER, set(Action)),
        (
            Role.ADMIN,
            {
                Action.MEMBERS_MANAGE,
                Action.GROUPS_MANAGE,
                Action.DOCUMENTS_MANAGE,
                Action.AUDITS_RUN,
                Action.CHAT_USE,
            },
        ),
        (Role.AUDITOR, {Action.AUDITS_RUN, Action.CHAT_USE}),
        (Role.MEMBER, {Action.CHAT_USE}),
    ],
)
def test_role_action_matrix(role: Role, allowed: set[Action]) -> None:
    for action in Action:
        assert role_allows(role, action) is (action in allowed)


def test_document_management_does_not_create_a_read_permission() -> None:
    assert role_allows(Role.ADMIN, Action.DOCUMENTS_MANAGE)
    assert "document_read" not in {action.value for action in Action}
