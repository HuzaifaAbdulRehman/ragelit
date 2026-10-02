import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import {
  type AccessCommand,
  type AccessResponse,
  type DocumentResponse,
  documentsApi,
} from "../../api/documents"
import { tenancyApi } from "../../api/tenancy"
import { Pagination } from "../shell/Pagination"
import { errorMessage, RequestError } from "../shell/RequestError"
import { useWorkspace } from "../shell/WorkspaceBoundary"

type Draft = Required<AccessCommand>
function draftFrom(access: AccessResponse): Draft {
  return {
    visibility:
      access.visibility === "organization" ? "organization" : "restricted",
    user_ids: [...access.user_ids],
    group_ids: [...access.group_ids],
  }
}
function fingerprint(draft: Draft): string {
  return JSON.stringify({
    visibility: draft.visibility,
    user_ids: [...draft.user_ids].sort(),
    group_ids: [...draft.group_ids].sort(),
  })
}
function AccessForm({
  documentId,
  cancel,
  saved,
}: {
  documentId: string
  cancel(): void
  saved(document: DocumentResponse): void
}) {
  const scope = useWorkspace()
  const client = useQueryClient()
  const [base, setBase] = useState<AccessResponse | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [usersOffset, setUsersOffset] = useState(0)
  const [groupsOffset, setGroupsOffset] = useState(0)
  const access = useQuery({
    queryKey: [...scope.key, "access", documentId],
    queryFn: ({ signal }) =>
      documentsApi.access(scope.token, documentId, signal),
    refetchOnMount: "always",
  })
  useEffect(() => {
    if (!base && access.data && !access.isFetching && !access.isError) {
      setBase(access.data)
      setDraft(draftFrom(access.data))
    }
  }, [base, access.data, access.isFetching, access.isError])
  const users = useQuery({
    queryKey: [...scope.key, "members", scope.organization.id, usersOffset],
    queryFn: ({ signal }) =>
      tenancyApi.members(
        scope.token,
        scope.organization.id,
        50,
        usersOffset,
        signal,
      ),
    enabled: Boolean(base),
  })
  const groups = useQuery({
    queryKey: [...scope.key, "groups", scope.organization.id, groupsOffset],
    queryFn: ({ signal }) =>
      tenancyApi.groups(
        scope.token,
        scope.organization.id,
        50,
        groupsOffset,
        signal,
      ),
    enabled: Boolean(base),
  })
  const mutation = useMutation({
    mutationFn: (command: Draft) =>
      documentsApi.saveAccess(scope.token, documentId, command, scope.signal),
    onSuccess: (document, command) => {
      if (scope.signal.aborted) return
      client.setQueryData([...scope.key, "access", documentId], command)
      saved(document)
    },
  })
  function select(
    kind: "user_ids" | "group_ids",
    id: string,
    checked: boolean,
  ) {
    mutation.reset()
    setDraft((current) => {
      if (!current) return current
      const ids = new Set(current[kind])
      if (checked) ids.add(id)
      else ids.delete(id)
      return { ...current, [kind]: [...ids] }
    })
  }
  const dirty = Boolean(
    base && draft && fingerprint(draftFrom(base)) !== fingerprint(draft),
  )
  const tooMany = Boolean(
    draft && (draft.user_ids.length > 100 || draft.group_ids.length > 100),
  )
  return (
    <section className="panel" aria-label="Document access editor">
      <h2>Document access</h2>
      <p>
        Administrator roles manage access; they do not automatically grant
        document reading.
      </p>
      {!base && access.isFetching && <p>Loading persisted access…</p>}
      <RequestError error={access.error} retry={() => void access.refetch()} />
      {base && (
        <p>
          Persisted access:{" "}
          {base.visibility === "organization"
            ? "Organization-wide"
            : "Restricted"}{" "}
          · {base.user_ids.length} user grants · {base.group_ids.length} group
          grants
        </p>
      )}
      {dirty && (
        <p className="muted">
          Unsaved changes. The persisted policy above still applies.
        </p>
      )}
      {draft && (
        <fieldset className="access-options" disabled={mutation.isPending}>
          <legend>Visibility</legend>
          <label className="check-label">
            <input
              type="radio"
              name="visibility"
              value="restricted"
              checked={draft.visibility === "restricted"}
              onChange={() => {
                mutation.reset()
                setDraft({ ...draft, visibility: "restricted" })
              }}
            />
            Restricted
          </label>
          <label className="check-label">
            <input
              type="radio"
              name="visibility"
              value="organization"
              checked={draft.visibility === "organization"}
              onChange={() => {
                mutation.reset()
                setDraft({
                  visibility: "organization",
                  user_ids: [],
                  group_ids: [],
                })
              }}
            />
            Organization-wide
          </label>
          <p>
            Organization-wide visibility allows every active member of this
            organization to read the document and clears explicit grants when
            saved.
          </p>
        </fieldset>
      )}
      {draft?.visibility === "restricted" && (
        <>
          <section aria-label="Selected grant targets">
            <h3>Selected targets</h3>
            <p>
              Targets outside this page, including inactive members, are
              retained until you explicitly remove them. Inactive memberships
              cannot retrieve evidence.
            </p>
            <ul className="record-list">
              {draft.user_ids.map((id) => (
                <li key={`user-${id}`}>
                  <span>
                    {users.data?.items.find((user) => user.user_id === id)
                      ?.email ?? id}
                  </span>{" "}
                  <button
                    type="button"
                    disabled={mutation.isPending}
                    aria-label={`Remove user ${id}`}
                    onClick={() => select("user_ids", id, false)}
                  >
                    Remove user
                  </button>
                </li>
              ))}
              {draft.group_ids.map((id) => (
                <li key={`group-${id}`}>
                  <span>
                    {groups.data?.items.find((group) => group.id === id)
                      ?.name ?? id}
                  </span>{" "}
                  <button
                    type="button"
                    disabled={mutation.isPending}
                    aria-label={`Remove group ${id}`}
                    onClick={() => select("group_ids", id, false)}
                  >
                    Remove group
                  </button>
                </li>
              ))}
            </ul>
          </section>
          <fieldset className="access-options" disabled={mutation.isPending}>
            <legend>Direct users</legend>
            {users.isPending && <p>Loading people…</p>}
            <RequestError
              error={users.error}
              retry={() => void users.refetch()}
            />
            {users.data?.items.map((user) => (
              <label className="check-label" key={user.id}>
                <input
                  type="checkbox"
                  checked={draft.user_ids.includes(user.user_id)}
                  disabled={!user.is_active}
                  onChange={(event) =>
                    select("user_ids", user.user_id, event.target.checked)
                  }
                />
                {user.email}
                {user.is_active ? "" : " (inactive)"}
              </label>
            ))}
            <Pagination
              label="users"
              offset={usersOffset}
              hasNext={users.data?.items.length === 50}
              pending={users.isFetching}
              onChange={setUsersOffset}
            />
          </fieldset>
          <fieldset className="access-options" disabled={mutation.isPending}>
            <legend>Groups</legend>
            {groups.isPending && <p>Loading groups…</p>}
            <RequestError
              error={groups.error}
              retry={() => void groups.refetch()}
            />
            {groups.data?.items.map((group) => (
              <label className="check-label" key={group.id}>
                <input
                  type="checkbox"
                  checked={draft.group_ids.includes(group.id)}
                  onChange={(event) =>
                    select("group_ids", group.id, event.target.checked)
                  }
                />
                {group.name}
              </label>
            ))}
            <Pagination
              label="grant groups"
              offset={groupsOffset}
              hasNext={groups.data?.items.length === 50}
              pending={groups.isFetching}
              onChange={setGroupsOffset}
            />
          </fieldset>
        </>
      )}
      {tooMany && (
        <p role="alert">Choose at most 100 user grants and 100 group grants.</p>
      )}
      {mutation.error && (
        <p className="form-error" role="alert">
          Access was not saved. {errorMessage(mutation.error)}
        </p>
      )}
      <div className="actions">
        <button
          type="button"
          disabled={
            !base ||
            !draft ||
            !dirty ||
            tooMany ||
            access.isFetching ||
            access.isError ||
            mutation.isPending
          }
          onClick={() => {
            if (draft)
              mutation.mutate(
                draft.visibility === "organization"
                  ? { visibility: "organization", user_ids: [], group_ids: [] }
                  : draft,
              )
          }}
        >
          {mutation.isPending
            ? "Saving access…"
            : mutation.error
              ? "Retry save access"
              : "Save access"}
        </button>
        <button type="button" disabled={mutation.isPending} onClick={cancel}>
          Cancel access
        </button>
      </div>
    </section>
  )
}
export function DocumentAccessEditor({
  documentId,
  onSaved,
}: {
  documentId: string
  onSaved(document: DocumentResponse): void
}) {
  const [editing, setEditing] = useState(false)
  const [saved, setSaved] = useState(false)
  return (
    <section>
      {!editing && (
        <button
          type="button"
          onClick={() => {
            setSaved(false)
            setEditing(true)
          }}
        >
          Edit access
        </button>
      )}
      {saved && <p role="status">Access saved</p>}
      {editing && (
        <AccessForm
          documentId={documentId}
          cancel={() => setEditing(false)}
          saved={(document) => {
            onSaved(document)
            setEditing(false)
            setSaved(true)
          }}
        />
      )}
    </section>
  )
}
