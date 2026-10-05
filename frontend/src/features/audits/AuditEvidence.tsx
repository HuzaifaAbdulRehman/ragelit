import type { AuditReport } from "../../api/audits"

export function AuditEvidence({ report }: { report: AuditReport }) {
  const ranked = [...report.results].sort(
    (a, b) =>
      ({ fail: 0, inconclusive: 1, pass: 2 })[a.status] -
      { fail: 0, inconclusive: 1, pass: 2 }[b.status],
  )
  return (
    <section>
      <h2>Saved boundary evidence</h2>
      <p>{report.scope_notice}</p>
      <p>{report.retrieval_notice}</p>
      <p>
        {report.results.length} of {report.required_case_ids.length} cases
        recorded. Coverage:{" "}
        {report.coverage_complete ? "complete" : "incomplete"}.
      </p>
      {report.inventory_reason && <p>Inventory: {report.inventory_reason}</p>}
      <ul className="record-list">
        {ranked.map((result) => (
          <li className="panel" key={result.case_id}>
            <details>
              <summary>
                {result.case_id} · {result.status}
              </summary>
              <p>
                Reason: {result.reason}. Coverage:{" "}
                {result.coverage_complete ? "complete" : "incomplete"}.
              </p>
              <p>
                Terminal: {result.observation.terminal}. HTTP status:{" "}
                {result.observation.http_status}.
              </p>
              <p>
                First exposure:{" "}
                {result.first_exposure
                  ? `${result.first_exposure} (event ${result.observation.boundaries.find((stage) => stage.boundary === result.first_exposure)?.sequence ?? "not provided"})`
                  : "none recorded"}
                .
              </p>
              <ol aria-label={`Stages for ${result.case_id}`}>
                {result.observation.boundaries.map((stage) => (
                  <li key={stage.boundary}>
                    <strong>{stage.boundary}</strong> · event {stage.sequence} ·{" "}
                    <span>{stage.state}</span>
                    <p>
                      Decision: {stage.decision}. Duration:{" "}
                      {stage.duration_ms.toFixed(2)} ms.{" "}
                      {stage.truncated
                        ? "Evidence truncated."
                        : "Evidence not truncated."}
                    </p>
                    <p>
                      Chunk IDs: {stage.chunk_ids.join(", ") || "none recorded"}
                    </p>
                    <p>
                      Canary matches:{" "}
                      {stage.canary_matches
                        .map(
                          (match) =>
                            `${match.canary_id} (${match.count} matches, offsets ${match.offsets?.join(", ") || "not provided"})`,
                        )
                        .join("; ") || "none recorded"}
                    </p>
                  </li>
                ))}
              </ol>
            </details>
          </li>
        ))}
      </ul>
    </section>
  )
}
