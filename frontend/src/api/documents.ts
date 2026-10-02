import { request } from "./client"
import type { components } from "./generated/schema"

export type DocumentResponse = components["schemas"]["DocumentResponse"]
export type VersionResponse = components["schemas"]["VersionResponse"]
export type AccessResponse = components["schemas"]["AccessResponse"]
export type AccessCommand = components["schemas"]["AccessCommand"]
const root = "/api/v1/documents"
const path = (id: string) => `${root}/${encodeURIComponent(id)}`
const binary = (file: File, signal?: AbortSignal): RequestInit => ({
  method: "POST",
  body: file,
  headers: { "Content-Type": "application/octet-stream" },
  signal,
})
export const documentsApi = {
  list: (token: string, limit: number, offset: number, signal?: AbortSignal) =>
    request<DocumentResponse[]>(
      root +
        "?" +
        new URLSearchParams({ limit: String(limit), offset: String(offset) }),
      { signal },
      token,
    ),
  get: (token: string, id: string, signal?: AbortSignal) =>
    request<DocumentResponse>(path(id), { signal }, token),
  access: (token: string, id: string, signal?: AbortSignal) =>
    request<AccessResponse>(`${path(id)}/access`, { signal }, token),
  upload: (token: string, file: File, signal?: AbortSignal) =>
    request<DocumentResponse>(
      `${root}?${new URLSearchParams({ filename: file.name })}`,
      binary(file, signal),
      token,
    ),
  versions: (token: string, id: string, signal?: AbortSignal) =>
    request<VersionResponse[]>(`${path(id)}/versions`, { signal }, token),
  replace: (token: string, id: string, file: File, signal?: AbortSignal) =>
    request<DocumentResponse>(
      `${path(id)}/versions?${new URLSearchParams({ filename: file.name })}`,
      binary(file, signal),
      token,
    ),
  retry: (token: string, id: string, signal?: AbortSignal) =>
    request<DocumentResponse>(
      `${path(id)}/retry`,
      { method: "POST", signal },
      token,
    ),
  saveAccess: (
    token: string,
    id: string,
    command: AccessCommand,
    signal?: AbortSignal,
  ) =>
    request<DocumentResponse>(
      path(id),
      { method: "PATCH", body: JSON.stringify(command), signal },
      token,
    ),
  remove: (token: string, id: string, signal?: AbortSignal) =>
    request<void>(path(id), { method: "DELETE", signal }, token),
}
