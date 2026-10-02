import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { documentsApi } from "../../api/documents"
import { Pagination } from "../shell/Pagination"
import { RequestError } from "../shell/RequestError"
import { useWorkspace } from "../shell/WorkspaceBoundary"
import { DocumentStatus, isIndexing } from "./DocumentStatus"
import { UploadDocumentForm } from "./UploadDocumentForm"

export function DocumentsPage() {
  const scope = useWorkspace()
  const client = useQueryClient()
  const navigate = useNavigate()
  const [offset, setOffset] = useState(0)
  const canManage = ["owner", "admin"].includes(scope.organization.role)
  const documents = useQuery({
    queryKey: [...scope.key, "documents", offset],
    queryFn: ({ signal }) => documentsApi.list(scope.token, 50, offset, signal),
    refetchInterval: (query) =>
      query.state.data?.some((item) => isIndexing(item.state)) ? 2000 : false,
    refetchIntervalInBackground: false,
  })
  return (
    <section>
      <p className="eyebrow">Workspace knowledge</p>
      <h1>Documents</h1>
      <p className="lede">
        {canManage
          ? "Manage indexing and reading access. A management role does not grant document reading."
          : "Documents your current access allows you to read."}
      </p>
      {canManage && (
        <UploadDocumentForm
          onComplete={(document) => {
            client.setQueryData(
              [...scope.key, "document", document.id],
              document,
            )
            void client.invalidateQueries({
              queryKey: [...scope.key, "documents"],
            })
            void navigate({
              to: "/documents/$documentId",
              params: { documentId: document.id },
            })
          }}
        />
      )}
      {documents.isPending && <p role="status">Loading documents…</p>}
      <RequestError
        error={documents.error}
        retry={() => void documents.refetch()}
      />
      {documents.data?.length === 0 && <p>No documents on this page.</p>}
      <ul className="record-list">
        {documents.data?.map((document) => (
          <li className="panel" key={document.id}>
            <Link
              to="/documents/$documentId"
              params={{ documentId: document.id }}
            >
              {document.filename}
            </Link>
            <p>
              <DocumentStatus state={document.state} /> · {document.visibility}
            </p>
          </li>
        ))}
      </ul>
      <Pagination
        label="documents"
        offset={offset}
        hasNext={documents.data?.length === 50}
        pending={documents.isFetching}
        onChange={setOffset}
      />
    </section>
  )
}
