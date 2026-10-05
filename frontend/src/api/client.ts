import type { components } from "./generated/schema"

export type LoginCommand = components["schemas"]["LoginCommand"]
export type Organization = components["schemas"]["OrganizationSummary"]
export type MemberList = components["schemas"]["MemberList"]
type TokenResponse = components["schemas"]["AccessTokenResponse"]
type ProblemDetail = components["schemas"]["ProblemDetail"]

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: ProblemDetail,
  ) {
    super(problem.detail)
  }
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
  accessToken?: string,
): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json")
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`)
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "include",
  })
  if (!response.ok) {
    let body: unknown
    try {
      body = await response.json()
    } catch {
      body = null
    }
    const safe: ProblemDetail = {
      type: "about:blank",
      title: "Request failed",
      code: "request_failed",
      status: response.status,
      detail: "Request failed. Please retry.",
    }
    if (
      body &&
      typeof body === "object" &&
      "code" in body &&
      typeof body.code === "string" &&
      "detail" in body &&
      typeof body.detail === "string"
    ) {
      throw new ApiError(response.status, {
        ...safe,
        ...body,
        code: body.code,
        detail: body.detail,
        status: response.status,
      })
    }
    throw new ApiError(response.status, safe)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export async function requestBlob(
  path: string,
  init: RequestInit = {},
  accessToken?: string,
): Promise<Blob> {
  const headers = new Headers(init.headers)
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`)
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "include",
  })
  if (!response.ok) {
    let body: unknown
    try {
      body = await response.json()
    } catch {
      body = null
    }
    const safe: ProblemDetail = {
      type: "about:blank",
      title: "Request failed",
      code: "request_failed",
      status: response.status,
      detail: "Request failed. Please retry.",
    }
    if (
      body &&
      typeof body === "object" &&
      "code" in body &&
      typeof body.code === "string" &&
      "detail" in body &&
      typeof body.detail === "string"
    )
      throw new ApiError(response.status, {
        ...safe,
        ...body,
        code: body.code,
        detail: body.detail,
        status: response.status,
      })
    throw new ApiError(response.status, safe)
  }
  return response.blob()
}

export const api = {
  login: (command: LoginCommand) =>
    request<TokenResponse>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify(command),
    }),
  refresh: () =>
    request<TokenResponse>("/api/v1/auth/refresh", { method: "POST" }),
  logout: (token: string) =>
    request<void>("/api/v1/auth/logout", { method: "POST" }, token),
  organizations: (token: string, signal?: AbortSignal) =>
    request<components["schemas"]["OrganizationList"]>(
      "/api/v1/organizations",
      { signal },
      token,
    ),
  switchOrganization: (token: string, targetOrganizationId: string) =>
    request<TokenResponse>(
      "/api/v1/auth/switch-organization",
      {
        method: "POST",
        body: JSON.stringify({ target_organization_id: targetOrganizationId }),
      },
      token,
    ),
  members: (token: string, organizationId: string) =>
    request<MemberList>(
      `/api/v1/organizations/${organizationId}/members`,
      {},
      token,
    ),
}
