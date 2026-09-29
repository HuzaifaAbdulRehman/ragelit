from enum import StrEnum

from app.tenancy.enums import Role


class Action(StrEnum):
    ORGANIZATION_MANAGE = "organization_manage"
    MEMBERS_MANAGE = "members_manage"
    GROUPS_MANAGE = "groups_manage"
    DOCUMENTS_MANAGE = "documents_manage"
    AUDITS_RUN = "audits_run"
    CHAT_USE = "chat_use"


_ROLE_ACTIONS: dict[Role, frozenset[Action]] = {
    Role.OWNER: frozenset(Action),
    Role.ADMIN: frozenset(
        {
            Action.MEMBERS_MANAGE,
            Action.GROUPS_MANAGE,
            Action.DOCUMENTS_MANAGE,
            Action.AUDITS_RUN,
            Action.CHAT_USE,
        }
    ),
    Role.AUDITOR: frozenset({Action.AUDITS_RUN, Action.CHAT_USE}),
    Role.MEMBER: frozenset({Action.CHAT_USE}),
}


def role_allows(role: Role, action: Action) -> bool:
    return action in _ROLE_ACTIONS[role]
