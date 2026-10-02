import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useParams } from "@tanstack/react-router"
import { useEffect, useState } from "react"
import { ApiError } from "../../api/client"
import { type MemberSummary, type Role, tenancyApi } from "../../api/tenancy"
import { useAuth } from "../auth/AuthProvider"
import { Pagination } from "../shell/Pagination"
import { RequestError } from "../shell/RequestError"
import { useWorkspace } from "../shell/WorkspaceBoundary"

function Person({
  member,
  organizationId,
}: {
  member: MemberSummary
  organizationId: string
}) {
  const scope = useWorkspace()
  const auth = useAuth()
  const client = useQueryClient()
  const [role, setRole] = useState<Role>(member.role)
  useEffect(() => {
    setRole(member.role)
  }, [member.role])
  const mutation = useMutation({
    mutationFn: (
      change:
        | { kind: "role"; role: Role }
        | { kind: "active"; active: boolean },
    ) =>
      change.kind === "role"
        ? tenancyApi.setRole(
            scope.token,
            organizationId,
            member.id,
            change.role,
            scope.signal,
          )
        : tenancyApi.setActive(
            scope.token,
            organizationId,
            member.id,
            change.active,
            scope.signal,
          ),
    onSuccess: async () => {
      if (scope.signal.aborted) return
      if (member.user_id === auth.currentUserId) {
        await auth.reloadMembership()
        return
      }
      await client.invalidateQueries({ queryKey: [...scope.key, "members"] })
    },
  })
  const protectedOwner =
    scope.organization.role === "admin" && member.role === "owner"
  return (
    <li className="person-card">
      <div>
        <strong>{member.email}</strong>
        <p>
          {member.is_active ? "Active" : "Inactive"} · {member.role}
        </p>
      </div>
      <form
        className="inline-form"
        onSubmit={(event) => {
          event.preventDefault()
          mutation.mutate({ kind: "role", role })
        }}
      >
        <label>
          Role for {member.email}
          <select
            value={role}
            onChange={(event) => setRole(event.target.value as Role)}
            disabled={mutation.isPending || protectedOwner}
          >
            {(["owner", "admin", "auditor", "member"] as const)
              .filter(
                (value) =>
                  scope.organization.role === "owner" ||
                  value !== "owner" ||
                  member.role === "owner",
              )
              .map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
          </select>
        </label>
        <button type="submit" disabled={mutation.isPending || protectedOwner}>
          Save role for {member.email}
        </button>
      </form>
      <button
        type="button"
        disabled={mutation.isPending || protectedOwner}
        onClick={() => {
          if (
            member.is_active &&
            !window.confirm(
              `Deactivate ${member.email}? Their workspace access will stop.`,
            )
          )
            return
          mutation.mutate({ kind: "active", active: !member.is_active })
        }}
      >
        {member.is_active ? "Deactivate " : "Activate "}
        {member.email}
      </button>
      <RequestError error={mutation.error} />
    </li>
  )
}
export function PeoplePage() {
  const { organizationId } = useParams({ strict: false })
  const scope = useWorkspace()
  const [offset, setOffset] = useState(0)
  const members = useQuery({
    queryKey: [...scope.key, "members", organizationId, offset],
    queryFn: ({ signal }) =>
      tenancyApi.members(scope.token, organizationId ?? "", 50, offset, signal),
  })
  if (members.error instanceof ApiError && members.error.status === 404)
    return (
      <section>
        <h1>Not found</h1>
        <p>The requested resource was not found.</p>
      </section>
    )
  return (
    <section>
      <p className="eyebrow">Access management</p>
      <h1>People</h1>
      <p className="lede">
        Manage existing memberships. Document access is assigned separately.
      </p>
      {members.isPending && <p role="status">Loading people…</p>}
      <RequestError
        error={members.error}
        retry={() => void members.refetch()}
      />
      {members.data?.items.length === 0 && <p>No people on this page.</p>}
      <ul className="record-list">
        {members.data?.items.map((member) => (
          <Person
            key={member.id}
            member={member}
            organizationId={organizationId ?? ""}
          />
        ))}
      </ul>
      <Pagination
        label="people"
        offset={offset}
        hasNext={members.data?.items.length === 50}
        pending={members.isFetching}
        onChange={setOffset}
      />
    </section>
  )
}
