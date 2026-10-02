export function Pagination({
  label,
  offset,
  hasNext,
  pending,
  onChange,
}: {
  label: string
  offset: number
  hasNext: boolean
  pending: boolean
  onChange(offset: number): void
}) {
  return (
    <fieldset className="pagination" aria-label={`${label} pages`}>
      <button
        type="button"
        disabled={pending || offset === 0}
        onClick={() => onChange(Math.max(0, offset - 50))}
      >
        Previous {label}
      </button>
      <span>Page {offset / 50 + 1}</span>
      <button
        type="button"
        disabled={pending || !hasNext}
        onClick={() => onChange(offset + 50)}
      >
        Next {label}
      </button>
    </fieldset>
  )
}
