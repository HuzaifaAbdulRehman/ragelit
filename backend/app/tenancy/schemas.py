from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.tenancy.enums import Role


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class OrganizationSummary(ApiModel):
    id: UUID
    name: str
    slug: str
    role: Role


class OrganizationList(ApiModel):
    items: list[OrganizationSummary]


class MemberSummary(ApiModel):
    id: UUID
    user_id: UUID
    email: str
    role: Role
    is_active: bool


class MemberList(ApiModel):
    items: list[MemberSummary]
    limit: int
    offset: int


class RoleChange(ApiModel):
    role: Role


class ActiveChange(ApiModel):
    is_active: bool


class GroupName(ApiModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("group name must not be blank")
        return normalized


class GroupCreate(GroupName):
    pass


class GroupRename(GroupName):
    pass


class GroupSummary(ApiModel):
    id: UUID
    name: str


class GroupList(ApiModel):
    items: list[GroupSummary]
    limit: int
    offset: int
