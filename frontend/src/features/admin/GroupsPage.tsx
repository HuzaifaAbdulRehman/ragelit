import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useParams } from "@tanstack/react-router"
import { useEffect, useState } from "react"
import { type GroupSummary, tenancyApi } from "../../api/tenancy"
import { Pagination } from "../shell/Pagination"
import { RequestError } from "../shell/RequestError"
import { useWorkspace } from "../shell/WorkspaceBoundary"
import { GroupMembersEditor } from "./GroupMembersEditor"

function GroupRow({
  group,
  organizationId,
  select,
  removed,
}: {
  group: GroupSummary
  organizationId: string
  select(group: GroupSummary): void
  removed(id: string): void
}) {
  const scope = useWorkspace()
  const client = useQueryClient()
  const [name, setName] = useState(group.name)
  useEffect(() => {
    setName(group.name)
  }, [group.name])
  const mutation = useMutation({
    mutationFn: async (kind: "rename" | "delete") => {
      if (kind === "rename") {
        await tenancyApi.renameGroup(
          scope.token,
          organizationId,
          group.id,
          name.trim(),
          scope.signal,
        )
      } else {
        await tenancyApi.removeGroup(
          scope.token,
          organizationId,
          group.id,
          scope.signal,
        )
      }
    },
    onSuccess: async (_, kind) => {
      if (scope.signal.aborted) return
      if (kind === "delete") removed(group.id)
      await client.invalidateQueries({ queryKey: [...scope.key, "groups"] })
    },
  })
  return (
    <li className="person-card">
      <form
        className="inline-form"
        onSubmit={(event) => {
          event.preventDefault()
          mutation.mutate("rename")
        }}
      >
        <label>
          Group name for {group.name}
          <input
            required
            maxLength={120}
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={mutation.isPending}
          />
        </label>
        <button type="submit" disabled={!name.trim() || mutation.isPending}>
          Rename {group.name}
        </button>
      </form>
      <div className="actions">
        <button
          type="button"
          disabled={mutation.isPending}
          onClick={() => select(group)}
        >
          Members for {group.name}
        </button>
        <button
          type="button"
          disabled={mutation.isPending}
          onClick={() => {
            if (window.confirm(`Delete ${group.name}? Group access will stop.`))
              mutation.mutate("delete")
          }}
        >
          Delete {group.name}
        </button>
      </div>
      <RequestError error={mutation.error} />
    </li>
  )
}
export function GroupsPage() {
  const { organizationId } = useParams({ strict: false })
  const scope = useWorkspace()
  const client = useQueryClient()
  const [offset, setOffset] = useState(0)
  const [name, setName] = useState("")
  const [selected, setSelected] = useState<GroupSummary | null>(null)
  const groups = useQuery({
    queryKey: [...scope.key, "groups", organizationId, offset],
    queryFn: ({ signal }) =>
      tenancyApi.groups(scope.token, organizationId ?? "", 50, offset, signal),
  })
  const create = useMutation({
    mutationFn: () =>
      tenancyApi.createGroup(
        scope.token,
        organizationId ?? "",
        name.trim(),
        scope.signal,
      ),
    onSuccess: async () => {
      if (scope.signal.aborted) return
      setName("")
      await client.invalidateQueries({ queryKey: [...scope.key, "groups"] })
    },
  })
  return (
    <section>
      <p className="eyebrow">Access management</p>
      <h1>Groups</h1>
      <p className="lede">
        Group membership can grant document reading without changing a person's
        role.
      </p>
      <form
        className="inline-form"
        onSubmit={(event) => {
          event.preventDefault()
          create.mutate()
        }}
      >
        <label>
          New group name
          <input
            value={name}
            required
            maxLength={120}
            disabled={create.isPending}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <button type="submit" disabled={!name.trim() || create.isPending}>
          Create group
        </button>
      </form>
      <RequestError error={create.error} />
      {groups.isPending && <p role="status">Loading groups…</p>}
      <RequestError error={groups.error} retry={() => void groups.refetch()} />
      {groups.data?.items.length === 0 && <p>No groups yet.</p>}
      <ul className="record-list">
        {groups.data?.items.map((group) => (
          <GroupRow
            key={group.id}
            group={group}
            organizationId={organizationId ?? ""}
            select={setSelected}
            removed={(id) => {
              if (selected?.id === id) setSelected(null)
            }}
          />
        ))}
      </ul>
      <Pagination
        label="groups"
        offset={offset}
        hasNext={groups.data?.items.length === 50}
        pending={groups.isFetching}
        onChange={(value) => {
          setSelected(null)
          setOffset(value)
        }}
      />
      {selected && (
        <GroupMembersEditor
          key={selected.id}
          group={selected}
          organizationId={organizationId ?? ""}
          close={() => setSelected(null)}
        />
      )}
    </section>
  )
}
