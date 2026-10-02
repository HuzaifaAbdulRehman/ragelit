import { expect, type Page } from "@playwright/test"
import type { components } from "../../src/api/generated/schema"

export const orgA = "11111111-1111-4111-8111-111111111111"
export const orgB = "22222222-2222-4222-8222-222222222222"
export const memberId = "33333333-3333-4333-8333-333333333333"
export const userId = "44444444-4444-4444-8444-444444444444"
export const groupId = "55555555-5555-4555-8555-555555555555"
export const documentId = "66666666-6666-4666-8666-666666666666"
export const runId = "77777777-7777-4777-8777-777777777777"
export const member: components["schemas"]["MemberSummary"] = {
  id: memberId,
  user_id: userId,
  email: "member@northstar.example",
  role: "member",
  is_active: true,
}
export const document: components["schemas"]["DocumentResponse"] = {
  id: documentId,
  filename: "handbook.txt",
  media_type: "text/plain",
  state: "ready",
  visibility: "restricted",
  created_at: "2026-10-02T10:00:00Z",
}
export function problem(code: string, status: number, detail: string) {
  return { type: "about:blank", title: "Request failed", code, status, detail }
}
export async function mockSession(
  page: Page,
  role: components["schemas"]["Role"] = "owner",
) {
  const token = (org: string) =>
    "fixture." +
    Buffer.from(JSON.stringify({ org })).toString("base64url") +
    ".fixture"
  await page.route("**/api/v1/auth/refresh", (route) =>
    route.fulfill({
      status: 401,
      json: problem("authentication_failed", 401, "Authentication failed."),
    }),
  )
  await page.route("**/api/v1/auth/login", (route) =>
    route.fulfill({
      json: { access_token: token(orgA), token_type: "bearer" },
    }),
  )
  await page.route("**/api/v1/auth/switch-organization", (route) =>
    route.fulfill({
      json: { access_token: token(orgB), token_type: "bearer" },
    }),
  )
  await page.route("**/api/v1/auth/logout", (route) =>
    route.fulfill({ status: 204 }),
  )
  await page.route("**/api/v1/organizations", (route) =>
    route.fulfill({
      json: {
        items: [
          { id: orgA, name: "Northstar Labs", slug: "northstar-labs", role },
          {
            id: orgB,
            name: "Harbor Works",
            slug: "harbor-works",
            role: "member",
          },
        ],
      },
    }),
  )
  await page.route(`**/api/v1/organizations/${orgA}/members*`, (route) =>
    route.fulfill({ json: { items: [member], limit: 50, offset: 0 } }),
  )
  await page.route(`**/api/v1/organizations/${orgA}/groups*`, (route) =>
    route.fulfill({
      json: {
        items: [{ id: groupId, name: "Engineering" }],
        limit: 50,
        offset: 0,
      },
    }),
  )
}
export async function signIn(
  page: Page,
  email = "owner@northstar.example",
  slug = "northstar-labs",
) {
  await page.goto("/login")
  await page.getByLabel("Work email").fill(email)
  await page.getByLabel("Password").fill("task-nine-test-password")
  await page.getByLabel("Organization", { exact: true }).fill(slug)
  await page.getByRole("button", { name: "Sign in", exact: true }).click()
  await expect(page.getByLabel("Organization switcher")).toBeVisible()
}
