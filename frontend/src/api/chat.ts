import { request } from "./client"
import type { components } from "./generated/schema"

export type QueryCommand = components["schemas"]["QueryCommand"]
export type AnswerResponse = components["schemas"]["AnswerResponse"]
export type TraceResponse = components["schemas"]["TraceResponse"]
export const chatApi = {
  query: (token: string, command: QueryCommand, signal?: AbortSignal) =>
    request<AnswerResponse>(
      "/api/v1/chat/query",
      { method: "POST", body: JSON.stringify(command), signal },
      token,
    ),
  trace: (token: string, id: string, signal?: AbortSignal) =>
    request<TraceResponse>(
      `/api/v1/query-runs/${encodeURIComponent(id)}`,
      { signal },
      token,
    ),
}
