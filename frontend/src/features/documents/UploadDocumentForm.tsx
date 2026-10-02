import { useMutation } from "@tanstack/react-query"
import { type FormEvent, useState } from "react"
import { type DocumentResponse, documentsApi } from "../../api/documents"
import { RequestError } from "../shell/RequestError"
import { useWorkspace } from "../shell/WorkspaceBoundary"

export function UploadDocumentForm({
  documentId,
  onComplete,
}: {
  documentId?: string
  onComplete(document: DocumentResponse): void
}) {
  const scope = useWorkspace()
  const [file, setFile] = useState<File | null>(null)
  const [validation, setValidation] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: (upload: File) =>
      documentId
        ? documentsApi.replace(scope.token, documentId, upload, scope.signal)
        : documentsApi.upload(scope.token, upload, scope.signal),
    onSuccess: (document) => {
      if (!scope.signal.aborted) onComplete(document)
    },
  })
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setValidation(null)
    mutation.reset()
    if (
      !file ||
      !["txt", "md", "docx", "pdf"].includes(
        file.name.split(".").at(-1)?.toLowerCase() ?? "",
      )
    ) {
      setValidation("Choose a TXT, Markdown, DOCX or text-based PDF file.")
      return
    }
    if (file.size > 25 * 1024 * 1024) {
      setValidation(
        "The default upload limit is 25 MiB. Choose a smaller file.",
      )
      return
    }
    if (file.size === 0) {
      setValidation("Choose a non-empty file.")
      return
    }
    mutation.mutate(file)
  }
  return (
    <form className="panel" onSubmit={submit}>
      <h2>{documentId ? "Replace document version" : "Upload a document"}</h2>
      <p className="muted">
        TXT, Markdown, DOCX and text-based PDF. Default limit: 25 MiB; server
        limits also apply. Image-only PDFs need OCR, which is not included.
      </p>
      {!documentId && (
        <p>
          New documents start restricted. Assign reading access after upload.
        </p>
      )}
      <label>
        {documentId ? "Replacement file" : "Document file"}
        <input
          type="file"
          accept=".txt,.md,.docx,.pdf"
          required
          disabled={mutation.isPending}
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null)
            setValidation(null)
            mutation.reset()
          }}
        />
      </label>
      {validation && (
        <p className="form-error" role="alert">
          {validation}
        </p>
      )}
      <RequestError error={mutation.error} />
      <button type="submit" disabled={mutation.isPending}>
        {mutation.isPending
          ? "Uploading…"
          : documentId
            ? "Replace version"
            : "Upload document"}
      </button>
    </form>
  )
}
