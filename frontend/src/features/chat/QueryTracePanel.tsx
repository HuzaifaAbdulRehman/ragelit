import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { chatApi } from "../../api/chat"
import { RequestError } from "../shell/RequestError"
import { useWorkspace } from "../shell/WorkspaceBoundary"

export function QueryTracePanel({ runId }: { runId: string }) {
  const scope = useWorkspace()
  const [open, setOpen] = useState(false)
  const trace = useQuery({
    queryKey: [...scope.key, "trace", runId],
    queryFn: ({ signal }) => chatApi.trace(scope.token, runId, signal),
    enabled: open,
  })
  return (
    <section className="panel">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "Hide query trace" : "View query trace"}
      </button>
      {open && (
        <section aria-label="Query trace">
          <h2>Query trace</h2>
          {trace.isPending && <p role="status">Loading trace…</p>}
          <RequestError
            error={trace.error}
            retry={() => void trace.refetch()}
          />
          {!trace.error && trace.data && (
            <>
              <p>State: {trace.data.state}</p>
              {trace.data.error_code && <p>Error: {trace.data.error_code}</p>}
              <ol>
                {trace.data.stages.map((stage) => (
                  <li key={JSON.stringify(stage)}>
                    <p>
                      {stage.stage} · {stage.decision} · {stage.duration_ms} ms
                    </p>
                    <ul>
                      {stage.chunk_ids.map((id) => (
                        <li key={id}>{id}</li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ol>
            </>
          )}
        </section>
      )}
    </section>
  )
}
