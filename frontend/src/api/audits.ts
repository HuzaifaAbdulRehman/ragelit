import { ApiError, request, requestBlob } from "./client"
import type { components } from "./generated/schema"

export type AuditRunSummary = components["schemas"]["AuditRunSummary"]
export type AuditRunList = components["schemas"]["AuditRunList"]
export type AuditRunDetail = components["schemas"]["AuditRunDetail"]
export type AuditReport = components["schemas"]["AuditReport"]
export const auditAllowed = (role: string) =>
  ["owner", "admin", "auditor"].includes(role)
export const auditPending = (state: AuditRunSummary["state"]) =>
  state === "queued" || state === "running"
export const auditDenied = (error: unknown) =>
  error instanceof ApiError && [401, 403].includes(error.status)
export const auditsApi = {
  start: (token: string, signal: AbortSignal) =>
    request<AuditRunSummary>(
      "/api/v1/audits",
      { method: "POST", body: "{}", signal },
      token,
    ),
  list: (token: string, limit: number, offset: number, signal: AbortSignal) =>
    request<AuditRunList>(
      `/api/v1/audits?limit=${limit}&offset=${offset}`,
      { signal },
      token,
    ),
  detail: (token: string, id: string, signal: AbortSignal) =>
    request<AuditRunDetail>(
      `/api/v1/audits/${encodeURIComponent(id)}`,
      { signal },
      token,
    ),
  download: (token: string, id: string, signal: AbortSignal) =>
    requestBlob(
      `/api/v1/audits/${encodeURIComponent(id)}/report.json`,
      { signal },
      token,
    ),
}
