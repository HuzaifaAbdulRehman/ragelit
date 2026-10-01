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

async function request<T>(
  path: string,
  init: RequestInit = {},
  accessToken?: string,
): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set("Content-Type", "application/json")
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`)
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "include",
  })
  if (!response.ok) {
    throw new ApiError(
      response.status,
      (await response.json()) as ProblemDetail,
    )
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
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
  organizations: (token: string) =>
    request<components["schemas"]["OrganizationList"]>(
      "/api/v1/organizations",
      {},
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
