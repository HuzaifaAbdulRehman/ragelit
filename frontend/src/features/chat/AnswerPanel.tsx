import type { AnswerResponse } from "../../api/chat"

export function AnswerPanel({ result }: { result: AnswerResponse }) {
  return (
    <section className="panel" aria-label="Answer">
      <h2>Answer</h2>
      {result.status === "abstained" ? (
        <p>No supporting evidence was found</p>
      ) : (
        <p className="answer-text">{result.answer}</p>
      )}
      {result.citations.length > 0 && (
        <ol aria-label="Citations">
          {result.citations.map((citation) => (
            <li key={citation.chunk_id}>
              {citation.filename} · {citation.location}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
