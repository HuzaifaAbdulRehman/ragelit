import { ApiError } from "../../api/client"

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.problem.code === "last_owner_required")
      return "The organization must keep its last active owner."
    return error.problem.detail
  }
  return "Request failed. Please retry."
}
export function RequestError({
  error,
  retry,
}: {
  error: unknown
  retry?: () => void
}) {
  if (!error) return null
  return (
    <div className="form-error" role="alert">
      <p>{errorMessage(error)}</p>
      {retry && (
        <button type="button" onClick={retry}>
          Retry
        </button>
      )}
    </div>
  )
}
