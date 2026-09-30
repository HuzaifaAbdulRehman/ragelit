from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TenantApiSeed:
    organization_a_id: UUID
    organization_a_name: str
    organization_a_slug: str
    organization_b_id: UUID
    organization_b_name: str
    organization_b_slug: str
    organization_c_id: UUID
    organization_c_name: str
    organization_d_id: UUID
    organization_d_name: str
    owner_email: str
    owner_membership_a_id: UUID
    owner_membership_b_id: UUID
    owner_membership_d_id: UUID
    admin_email: str
    admin_membership_id: UUID
    auditor_email: str
    member_email: str
    member_membership_id: UUID
    outsider_email: str
    outsider_membership_b_id: UUID
    group_a_id: UUID
    group_a_name: str
    group_b_id: UUID
    group_b_name: str
