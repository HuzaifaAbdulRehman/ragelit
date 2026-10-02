import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate, useParams } from "@tanstack/react-router"
import { type DocumentResponse, documentsApi } from "../../api/documents"
import { RequestError } from "../shell/RequestError"
import { useWorkspace } from "../shell/WorkspaceBoundary"
import { DocumentStatus, isIndexing } from "./DocumentStatus"
import { UploadDocumentForm } from "./UploadDocumentForm"

export function DocumentDetailPage() {
  const { documentId = "" } = useParams({ strict: false })
  const scope = useWorkspace()
  const client = useQueryClient()
  const navigate = useNavigate()
  const canManage = ["owner", "admin"].includes(scope.organization.role)
  const key = [...scope.key, "document", documentId]
  const document = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => documentsApi.get(scope.token, documentId, signal),
    refetchInterval: (query) =>
      query.state.data && isIndexing(query.state.data.state) ? 2000 : false,
    refetchIntervalInBackground: false,
  })
  const versions = useQuery({
    queryKey: [...scope.key, "versions", documentId],
    queryFn: ({ signal }) =>
      documentsApi.versions(scope.token, documentId, signal),
    enabled: Boolean(document.data),
    refetchInterval:
      document.data && isIndexing(document.data.state) ? 2000 : false,
    refetchIntervalInBackground: false,
  })
  const action = useMutation<
    DocumentResponse | undefined,
    unknown,
    "retry" | "delete"
  >({
    mutationFn: async (kind) => {
      if (kind === "retry")
        return await documentsApi.retry(scope.token, documentId, scope.signal)
      await documentsApi.remove(scope.token, documentId, scope.signal)
    },
    onSuccess: async (updated, kind) => {
      if (scope.signal.aborted) return
      if (updated) client.setQueryData(key, updated)
      await client.invalidateQueries({ queryKey: [...scope.key, "documents"] })
      if (kind === "delete") {
        await navigate({ to: "/documents" })
        return
      }
      await client.invalidateQueries({ queryKey: key })
      await client.invalidateQueries({
        queryKey: [...scope.key, "versions", documentId],
      })
    },
  })
  if (!document.data)
    return (
      <section>
        <h1>Document</h1>
        {document.isPending && <p role="status">Loading document…</p>}
        <RequestError
          error={document.error}
          retry={() => void document.refetch()}
        />
      </section>
    )
  return (
    <section>
      <p className="eyebrow">Document details</p>
      <h1>{document.data.filename}</h1>
      <p>
        <DocumentStatus live state={document.data.state} /> ·{" "}
        {document.data.visibility}
      </p>
      <RequestError
        error={document.error}
        retry={() => void document.refetch()}
      />
      {document.data.state === "failed" && (
        <p className="form-error">
          Indexing failed. Retry indexing or replace this version with a
          supported text-based file.
        </p>
      )}
      {canManage && (
        <div className="actions">
          {document.data.state === "failed" && (
            <button
              type="button"
              disabled={action.isPending}
              onClick={() => action.mutate("retry")}
            >
              Retry indexing
            </button>
          )}
          <button
            type="button"
            disabled={action.isPending}
            onClick={() => {
              if (
                window.confirm(
                  `Delete ${document.data?.filename}? Reading access will stop.`,
                )
              )
                action.mutate("delete")
            }}
          >
            Delete document
          </button>
        </div>
      )}
      <RequestError error={action.error} />
      <section className="panel">
        <h2>Version history</h2>
        {versions.isPending && <p>Loading versions…</p>}
        <RequestError
          error={versions.error}
          retry={() => void versions.refetch()}
        />
        {versions.data?.length === 0 && <p>No versions returned.</p>}
        <ol aria-label="Version history">
          {versions.data?.map((version) => (
            <li key={version.id}>
              <DocumentStatus state={version.state} /> · {version.chunk_count}{" "}
              chunks · {version.created_at}
            </li>
          ))}
        </ol>
      </section>
      {canManage && !isIndexing(document.data.state) && (
        <UploadDocumentForm
          documentId={documentId}
          onComplete={(updated) => {
            action.reset()
            client.setQueryData(key, updated)
            void client.invalidateQueries({ queryKey: key })
            void client.invalidateQueries({
              queryKey: [...scope.key, "versions", documentId],
            })
          }}
        />
      )}
    </section>
  )
}
