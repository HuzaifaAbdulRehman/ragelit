import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { type GroupSummary, tenancyApi } from "../../api/tenancy"
import { Pagination } from "../shell/Pagination"
import { RequestError } from "../shell/RequestError"
import { useWorkspace } from "../shell/WorkspaceBoundary"

export function GroupMembersEditor({
  group,
  organizationId,
  close,
}: {
  group: GroupSummary
  organizationId: string
  close(): void
}) {
  const scope = useWorkspace()
  const client = useQueryClient()
  const [offset, setOffset] = useState(0)
  const [peopleOffset, setPeopleOffset] = useState(0)
  const [selected, setSelected] = useState("")
  const members = useQuery({
    queryKey: [...scope.key, "group-members", group.id, offset],
    queryFn: ({ signal }) =>
      tenancyApi.groupMembers(
        scope.token,
        organizationId,
        group.id,
        50,
        offset,
        signal,
      ),
  })
  const people = useQuery({
    queryKey: [...scope.key, "members", organizationId, peopleOffset],
    queryFn: ({ signal }) =>
      tenancyApi.members(scope.token, organizationId, 50, peopleOffset, signal),
  })
  const mutation = useMutation({
    mutationFn: ({ id, add }: { id: string; add: boolean }) =>
      add
        ? tenancyApi.addGroupMember(
            scope.token,
            organizationId,
            group.id,
            id,
            scope.signal,
          )
        : tenancyApi.removeGroupMember(
            scope.token,
            organizationId,
            group.id,
            id,
            scope.signal,
          ),
    onSuccess: async () => {
      if (scope.signal.aborted) return
      setSelected("")
      await client.invalidateQueries({
        queryKey: [...scope.key, "group-members", group.id],
      })
    },
  })
  return (
    <section className="panel" aria-label={`Members of ${group.name}`}>
      <h2>Members of {group.name}</h2>
      <button type="button" onClick={close}>
        Close members
      </button>
      {members.isPending && <p role="status">Loading group members…</p>}
      <RequestError
        error={members.error}
        retry={() => void members.refetch()}
      />
      <RequestError error={mutation.error} />
      {members.data?.items.length === 0 && <p>No members on this page.</p>}
      {members.data?.items.map((member) => (
        <label className="check-label" key={member.id}>
          <input
            type="checkbox"
            checked
            disabled={mutation.isPending}
            onChange={() => {
              if (window.confirm(`Remove ${member.email} from ${group.name}?`))
                mutation.mutate({ id: member.id, add: false })
            }}
          />
          {member.email}
        </label>
      ))}
      <Pagination
        label="group members"
        offset={offset}
        hasNext={members.data?.items.length === 50}
        pending={members.isFetching || mutation.isPending}
        onChange={setOffset}
      />
      <form
        className="inline-form"
        onSubmit={(event) => {
          event.preventDefault()
          if (selected) mutation.mutate({ id: selected, add: true })
        }}
      >
        <label>
          Add person
          <select
            value={selected}
            disabled={people.isFetching || mutation.isPending}
            onChange={(event) => setSelected(event.target.value)}
          >
            <option value="">Choose an active member</option>
            {people.data?.items.map((member) => (
              <option
                key={member.id}
                value={member.id}
                disabled={!member.is_active}
              >
                {member.email}
                {member.is_active ? "" : " (inactive)"}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          disabled={!selected || mutation.isPending || people.isFetching}
        >
          Add member
        </button>
      </form>
      <RequestError error={people.error} retry={() => void people.refetch()} />
      <Pagination
        label="available people"
        offset={peopleOffset}
        hasNext={people.data?.items.length === 50}
        pending={people.isFetching || mutation.isPending}
        onChange={(value) => {
          setSelected("")
          setPeopleOffset(value)
        }}
      />
    </section>
  )
}
