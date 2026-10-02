import { useMutation } from "@tanstack/react-query"
import { useRef, useState } from "react"
import { type AnswerResponse, chatApi } from "../../api/chat"
import { ApiError } from "../../api/client"
import { errorMessage } from "../shell/RequestError"
import { useWorkspace } from "../shell/WorkspaceBoundary"
import { AnswerPanel } from "./AnswerPanel"
import { QueryTracePanel } from "./QueryTracePanel"

function traceId(value: unknown): string | null {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      value,
    )
    ? value
    : null
}

export function ChatPage() {
  const scope = useWorkspace()
  const [question, setQuestion] = useState("")
  const [lastQuestion, setLastQuestion] = useState("")
  const [result, setResult] = useState<AnswerResponse | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [cancelled, setCancelled] = useState(false)
  const [validation, setValidation] = useState("")
  const active = useRef<AbortController | null>(null)
  const query = useMutation({
    mutationFn: ({
      text,
      controller,
    }: {
      text: string
      controller: AbortController
    }) =>
      chatApi.query(
        scope.token,
        { question: text, limit: 10 },
        AbortSignal.any([scope.signal, controller.signal]),
      ),
    onSuccess: (answer, { controller }) => {
      if (
        scope.signal.aborted ||
        controller.signal.aborted ||
        active.current !== controller
      )
        return
      setResult(answer)
      setRunId(traceId(answer.query_run_id))
    },
    onError: (failure, { controller }) => {
      if (
        scope.signal.aborted ||
        controller.signal.aborted ||
        active.current !== controller
      )
        return
      setError(failure)
      if (failure instanceof ApiError) {
        const extension: unknown = failure.problem
        if (
          extension &&
          typeof extension === "object" &&
          "query_run_id" in extension
        )
          setRunId(traceId(extension.query_run_id))
      }
    },
  })
  function submit(text: string) {
    const trimmed = text.trim()
    if (trimmed.length < 1 || trimmed.length > 4000) {
      setValidation("Enter a question of 1 to 4000 characters.")
      return
    }
    active.current?.abort()
    const controller = new AbortController()
    active.current = controller
    setLastQuestion(trimmed)
    setResult(null)
    setRunId(null)
    setError(null)
    setCancelled(false)
    setValidation("")
    query.mutate({ text: trimmed, controller })
  }
  return (
    <section>
      <p className="eyebrow">Ask your documents</p>
      <h1>Chat</h1>
      <p className="lede">
        Answers use evidence you can currently read. Check the citations before
        relying on an answer.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          submit(question)
        }}
      >
        <label>
          Your question
          <textarea
            value={question}
            maxLength={4000}
            rows={4}
            disabled={query.isPending}
            onChange={(event) => setQuestion(event.target.value)}
          />
        </label>
        {validation && (
          <p role="alert" className="form-error">
            {validation}
          </p>
        )}
        <button type="submit" disabled={query.isPending}>
          Ask question
        </button>
      </form>
      {query.isPending && (
        <div className="actions">
          <p role="status">Finding evidence and preparing an answer…</p>
          <button
            type="button"
            onClick={() => {
              active.current?.abort()
              query.reset()
              setCancelled(true)
            }}
          >
            Cancel question
          </button>
        </div>
      )}
      {cancelled && <p role="status">Request cancelled.</p>}
      {Boolean(error) && (
        <div className="form-error" role="alert">
          <p>{errorMessage(error)}</p>
          {error instanceof ApiError &&
            error.problem.code === "generation_not_configured" && (
              <p>
                An operator must configure the server-side LLM endpoint and
                model. No provider key belongs in this browser.
              </p>
            )}
        </div>
      )}
      {(Boolean(error) || cancelled) && !query.isPending && (
        <button type="button" onClick={() => submit(lastQuestion)}>
          Retry question
        </button>
      )}
      {result && <AnswerPanel result={result} />}
      {runId && <QueryTracePanel key={runId} runId={runId} />}
    </section>
  )
}
