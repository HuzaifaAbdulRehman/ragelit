import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { useEffect, useState } from "react"
import {
  auditAllowed,
  auditDenied,
  auditPending,
  auditsApi,
} from "../../api/audits"
import { RequestError } from "../shell/RequestError"
import { useWorkspace } from "../shell/WorkspaceBoundary"

export function AuditsPage() {
  const scope = useWorkspace()
  return auditAllowed(scope.organization.role) ? (
    <AuditList />
  ) : (
    <AuditAccessDenied />
  )
}
export function AuditAccessDenied() {
  return (
    <section>
      <h1>Audit access denied</h1>
      <p>Only owners, admins, and auditors can use synthetic audits.</p>
    </section>
  )
}
function AuditList() {
  const scope = useWorkspace()
  const client = useQueryClient()
  const navigate = useNavigate()
  const [offset, setOffset] = useState(0)
  const [denied, setDenied] = useState(false)
  const runs = useQuery({
    queryKey: [...scope.key, "audits", "list", offset],
    enabled: !denied,
    queryFn: ({ signal }) =>
      auditsApi.list(
        scope.token,
        20,
        offset,
        AbortSignal.any([signal, scope.signal]),
      ),
    refetchInterval: (query) =>
      !query.state.error &&
      query.state.data?.items.some((run) => auditPending(run.state))
        ? 2000
        : false,
    refetchIntervalInBackground: false,
  })
  const start = useMutation({
    mutationFn: () => auditsApi.start(scope.token, scope.signal),
    onSuccess: async (run) => {
      if (scope.signal.aborted) return
      client.setQueryData([...scope.key, "audits", "detail", run.id], {
        ...run,
        report: null,
      })
      await client.invalidateQueries({
        queryKey: [...scope.key, "audits", "list"],
      })
      if (!scope.signal.aborted)
        await navigate({ to: "/audits/$auditId", params: { auditId: run.id } })
    },
  })
  const blocked = auditDenied(runs.error) || auditDenied(start.error)
  useEffect(() => {
    if (!blocked) return
    setDenied(true)
    void client.cancelQueries({ queryKey: [...scope.key, "audits"] })
    client.removeQueries({ queryKey: [...scope.key, "audits"] })
  }, [blocked, client, scope.key])
  if (denied || blocked) return <AuditAccessDenied />
  const outstanding = runs.data?.items.some(
    (run) => auditPending(run.state) || run.state === "recovery_required",
  )
  return (
    <section>
      <p className="eyebrow">Synthetic access-control checks</p>
      <h1>Audits</h1>
      <p className="lede">
        Run the fixed 51-case safe pack against invented fixtures. This does not
        test company uploads or certify your workspace as secure.
      </p>
      <button
        type="button"
        disabled={
          start.isPending ||
          outstanding ||
          runs.isPending ||
          Boolean(runs.error)
        }
        onClick={() => start.mutate()}
      >
        {start.isPending ? "Queueing audit..." : "Start synthetic audit"}
      </button>
      {outstanding && (
        <p>
          An audit is outstanding. Finish it or resolve recovery before starting
          another.
        </p>
      )}
      {runs.isPending && <p role="status">Loading audits...</p>}
      <RequestError error={runs.error} retry={() => void runs.refetch()} />
      <RequestError error={start.error} />
      {runs.data?.total === 0 && (
        <p>
          No audits yet. Start a synthetic audit to collect boundary evidence.
        </p>
      )}
      <ul className="record-list">
        {runs.data?.items.map((run) => (
          <li className="panel" key={run.id}>
            <Link to="/audits/$auditId" params={{ auditId: run.id }}>
              {run.created_at}
            </Link>
            <p>
              {run.state} · {run.outcome} ·{" "}
              {run.report_available ? "Report available" : "No report yet"}
            </p>
          </li>
        ))}
      </ul>
      <nav className="pagination" aria-label="Audit pages">
        <button
          type="button"
          disabled={offset === 0 || runs.isFetching}
          onClick={() => setOffset(Math.max(0, offset - 20))}
        >
          Previous audits
        </button>
        <span>Page {offset / 20 + 1}</span>
        <button
          type="button"
          disabled={
            runs.isFetching || !runs.data || offset + 20 >= runs.data.total
          }
          onClick={() => setOffset(offset + 20)}
        >
          Next audits
        </button>
      </nav>
    </section>
  )
}
