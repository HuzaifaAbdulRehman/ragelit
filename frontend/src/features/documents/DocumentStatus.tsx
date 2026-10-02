export function isIndexing(state: string): boolean {
  return state === "queued" || state === "processing"
}
export function DocumentStatus({
  state,
  live = false,
}: {
  state: string
  live?: boolean
}) {
  const text: Record<string, string> = {
    queued: "Queued",
    processing: "Processing",
    ready: "Ready",
    failed: "Failed",
    deleted: "Deleted",
    superseded: "Superseded",
  }
  return (
    <span className="document-status" role={live ? "status" : undefined}>
      {text[state] ?? state}
    </span>
  )
}
