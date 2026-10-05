import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useParams } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"
import {
  auditAllowed,
  auditDenied,
  auditPending,
  auditsApi,
} from "../../api/audits"
import { RequestError } from "../shell/RequestError"
import { useWorkspace } from "../shell/WorkspaceBoundary"
import { AuditEvidence } from "./AuditEvidence"
import { AuditAccessDenied } from "./AuditsPage"

export function AuditDetailPage() {
  const scope = useWorkspace()
  return auditAllowed(scope.organization.role) ? (
    <AuditDetail />
  ) : (
    <AuditAccessDenied />
  )
}
function AuditDetail() {
  const { auditId = "" } = useParams({ strict: false })
  const scope = useWorkspace()
  const client = useQueryClient()
  const [denied, setDenied] = useState(false)
  const [downloadController] = useState(() => new AbortController())
  const objectUrl = useRef<string | null>(null)
  const run = useQuery({
    queryKey: [...scope.key, "audits", "detail", auditId],
    enabled: !denied,
    queryFn: ({ signal }) =>
      auditsApi.detail(
        scope.token,
        auditId,
        AbortSignal.any([signal, scope.signal]),
      ),
    refetchInterval: (query) =>
      !query.state.error &&
      query.state.data &&
      auditPending(query.state.data.state)
        ? 2000
        : false,
    refetchIntervalInBackground: false,
  })
  const download = useMutation({
    mutationFn: () =>
      auditsApi.download(
        scope.token,
        auditId,
        AbortSignal.any([scope.signal, downloadController.signal]),
      ),
    onSuccess: (blob) => {
      if (scope.signal.aborted || downloadController.signal.aborted) return
      const url = URL.createObjectURL(blob)
      objectUrl.current = url
      const link = document.createElement("a")
      link.href = url
      link.download = `${run.data?.report_id ?? auditId}.json`
      link.click()
      setTimeout(() => {
        URL.revokeObjectURL(url)
        if (objectUrl.current === url) objectUrl.current = null
      }, 0)
    },
  })
  const blocked = auditDenied(run.error) || auditDenied(download.error)
  useEffect(() => {
    if (!blocked) return
    setDenied(true)
    downloadController.abort()
    void client.cancelQueries({ queryKey: [...scope.key, "audits"] })
    client.removeQueries({ queryKey: [...scope.key, "audits"] })
  }, [blocked, client, downloadController, scope.key])
  useEffect(() => {
    const revoke = () => {
      downloadController.abort()
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
      objectUrl.current = null
    }
    scope.signal.addEventListener("abort", revoke)
    return () => {
      scope.signal.removeEventListener("abort", revoke)
      revoke()
    }
  }, [scope.signal, downloadController])
  if (denied || blocked) return <AuditAccessDenied />
  return (
    <section>
      <Link to="/audits">All audits</Link>
      <h1>Synthetic audit</h1>
      {run.isPending && <p role="status">Loading audit...</p>}
      <RequestError error={run.error} retry={() => void run.refetch()} />
      {run.data && (
        <>
          <dl>
            <dt>Job state</dt>
            <dd data-testid="audit-state">{run.data.state}</dd>
            <dt>Outcome</dt>
            <dd data-testid="audit-outcome">{run.data.outcome}</dd>
            <dt>Exit code</dt>
            <dd>{run.data.exit_code ?? "not provided"}</dd>
            <dt>Request ID</dt>
            <dd>{run.data.id}</dd>
            <dt>Report ID</dt>
            <dd>{run.data.report_id ?? "not provided"}</dd>
          </dl>
          {run.data.state === "queued" && (
            <p>
              Waiting for the operator's audit worker. No checks have run yet.
            </p>
          )}
          {run.data.state === "running" && (
            <p role="status">
              Running the synthetic safe pack. Results appear when the worker
              finishes.
            </p>
          )}
          {run.data.state === "recovery_required" && (
            <p role="alert">
              Confirm the worker and its child have stopped, then use the
              operator recovery command.
            </p>
          )}
          {run.data.error_code && (
            <p className="form-error">
              Audit stopped: {run.data.error_code}. This is not a passing audit.
            </p>
          )}
          {run.data.report_available && (
            <button
              type="button"
              disabled={download.isPending}
              onClick={() => download.mutate()}
            >
              {download.isPending ? "Downloading..." : "Download JSON"}
            </button>
          )}
          <RequestError error={download.error} />
          {run.data.report ? (
            <AuditEvidence report={run.data.report} />
          ) : (
            <p>No validated report is available.</p>
          )}
        </>
      )}
    </section>
  )
}
