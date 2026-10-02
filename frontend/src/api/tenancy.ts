import { request } from "./client"
import type { components } from "./generated/schema"

export type MemberSummary = components["schemas"]["MemberSummary"]
export type MemberList = components["schemas"]["MemberList"]
export type GroupSummary = components["schemas"]["GroupSummary"]
export type GroupList = components["schemas"]["GroupList"]
export type Role = components["schemas"]["Role"]
const root = (org: string) => `/api/v1/organizations/${encodeURIComponent(org)}`
const group = (org: string, id: string) =>
  `${root(org)}/groups/${encodeURIComponent(id)}`
const page = (limit: number, offset: number) =>
  `?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`
const command = (
  method: string,
  body: unknown,
  signal?: AbortSignal,
): RequestInit => ({ method, body: JSON.stringify(body), signal })
export const tenancyApi = {
  members: (
    token: string,
    org: string,
    limit: number,
    offset: number,
    signal?: AbortSignal,
  ) =>
    request<MemberList>(
      `${root(org)}/members${page(limit, offset)}`,
      { signal },
      token,
    ),
  groups: (
    token: string,
    org: string,
    limit: number,
    offset: number,
    signal?: AbortSignal,
  ) =>
    request<GroupList>(
      `${root(org)}/groups${page(limit, offset)}`,
      { signal },
      token,
    ),
  groupMembers: (
    token: string,
    org: string,
    id: string,
    limit: number,
    offset: number,
    signal?: AbortSignal,
  ) =>
    request<MemberList>(
      `${group(org, id)}/members${page(limit, offset)}`,
      { signal },
      token,
    ),
  setRole: (
    token: string,
    org: string,
    id: string,
    role: Role,
    signal?: AbortSignal,
  ) =>
    request<MemberSummary>(
      `${root(org)}/members/${encodeURIComponent(id)}/role`,
      command("PATCH", { role }, signal),
      token,
    ),
  setActive: (
    token: string,
    org: string,
    id: string,
    isActive: boolean,
    signal?: AbortSignal,
  ) =>
    request<MemberSummary>(
      `${root(org)}/members/${encodeURIComponent(id)}/active`,
      command("PATCH", { is_active: isActive }, signal),
      token,
    ),
  createGroup: (
    token: string,
    org: string,
    name: string,
    signal?: AbortSignal,
  ) =>
    request<GroupSummary>(
      `${root(org)}/groups`,
      command("POST", { name }, signal),
      token,
    ),
  renameGroup: (
    token: string,
    org: string,
    id: string,
    name: string,
    signal?: AbortSignal,
  ) =>
    request<GroupSummary>(
      group(org, id),
      command("PATCH", { name }, signal),
      token,
    ),
  removeGroup: (token: string, org: string, id: string, signal?: AbortSignal) =>
    request<void>(group(org, id), { method: "DELETE", signal }, token),
  addGroupMember: (
    token: string,
    org: string,
    id: string,
    membershipId: string,
    signal?: AbortSignal,
  ) =>
    request<void>(
      `${group(org, id)}/members/${encodeURIComponent(membershipId)}`,
      { method: "POST", signal },
      token,
    ),
  removeGroupMember: (
    token: string,
    org: string,
    id: string,
    membershipId: string,
    signal?: AbortSignal,
  ) =>
    request<void>(
      `${group(org, id)}/members/${encodeURIComponent(membershipId)}`,
      { method: "DELETE", signal },
      token,
    ),
}
