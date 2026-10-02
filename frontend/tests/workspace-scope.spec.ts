import { expect, test } from "@playwright/test"
import {
  document,
  member,
  mockSession,
  orgA,
  problem,
  signIn,
} from "./helpers/session"

test.beforeEach(async ({ page }) => mockSession(page))

test("switch hides private content before the new session arrives", async ({
  page,
}) => {
  await signIn(page)
  await page.route(`**/api/v1/organizations/${orgA}/members*`, (route) =>
    route.fulfill({
      json: {
        items: [{ ...member, email: "Old workspace secret" }],
        limit: 50,
        offset: 0,
      },
    }),
  )
  await page.getByRole("link", { name: "People" }).click()
  await expect(
    page.getByText("Old workspace secret", { exact: true }),
  ).toBeVisible()
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route("**/api/v1/auth/switch-organization", async (route) => {
    await gate
    await route.fallback()
  })
  await page
    .getByLabel("Organization switcher")
    .selectOption({ label: "Harbor Works" })
  await expect(page.getByText("Old workspace secret")).toHaveCount(0)
  release()
  await expect(
    page.getByRole("heading", { name: "Harbor Works" }),
  ).toBeVisible()
})

test("a delayed old request cannot populate the switched workspace", async ({
  page,
}) => {
  await signIn(page)
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  let started!: () => void
  const waiting = new Promise<void>((resolve) => {
    started = resolve
  })
  await page.route(
    `**/api/v1/organizations/${orgA}/members*`,
    async (route) => {
      started()
      await gate
      await route
        .fulfill({
          json: {
            items: [{ ...member, email: "Old workspace secret" }],
            limit: 50,
            offset: 0,
          },
        })
        .catch(() => {})
    },
  )
  await page.getByRole("link", { name: "People" }).click()
  await waiting
  await page
    .getByLabel("Organization switcher")
    .selectOption({ label: "Harbor Works" })
  await expect(
    page.getByRole("heading", { name: "Harbor Works" }),
  ).toBeVisible()
  release()
  await expect(page.getByText("Old workspace secret")).toHaveCount(0)
})

test("logout during a request hides the view before revocation finishes", async ({
  page,
}) => {
  await signIn(page)
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route("**/api/v1/auth/logout", async (route) => {
    await gate
    await route.fallback()
  })
  await page.getByRole("link", { name: "People" }).click()
  await expect(page.getByText(member.email, { exact: true })).toBeVisible()
  await page.getByRole("button", { name: "Log out", exact: true }).click()
  await expect(
    page.getByRole("heading", { name: "Welcome back" }),
  ).toBeVisible()
  release()
  await expect(page.getByText(member.email)).toHaveCount(0)
})

test("failed logout hides data and offers revocation retry", async ({
  page,
}) => {
  await signIn(page)
  await page.route("**/api/v1/auth/logout", (route) =>
    route.fulfill({
      status: 503,
      json: problem("unavailable", 503, "Unavailable"),
    }),
  )
  await page.getByRole("button", { name: "Log out", exact: true }).click()
  await expect(
    page.getByRole("heading", { name: "Welcome back" }),
  ).toBeVisible()
  await expect(page.getByRole("alert")).toContainText("revocation")
  await page.unroute("**/api/v1/auth/logout")
  await page.route("**/api/v1/auth/logout", (route) =>
    route.fulfill({ status: 204 }),
  )
  await page.getByRole("button", { name: "Retry log out" }).click()
  await expect(page.getByRole("button", { name: "Retry log out" })).toHaveCount(
    0,
  )
})

test("expired session clears private state", async ({ page }) => {
  await signIn(page)
  await page.route(`**/api/v1/organizations/${orgA}/members*`, (route) =>
    route.fulfill({
      status: 401,
      json: problem("authentication_failed", 401, "Authentication failed."),
    }),
  )
  await page.getByRole("link", { name: "People" }).click()
  await expect(
    page.getByRole("heading", { name: "Welcome back" }),
  ).toBeVisible()
})

test("non-JSON failures produce a safe visible error", async ({ page }) => {
  await signIn(page)
  await page.route(`**/api/v1/organizations/${orgA}/members*`, (route) =>
    route.fulfill({
      status: 503,
      contentType: "text/html",
      body: "<b>internal proxy secret</b>",
    }),
  )
  await page.getByRole("link", { name: "People" }).click()
  await expect(page.getByRole("alert")).toContainText("Request failed")
  await expect(page.getByText("internal proxy secret")).toHaveCount(0)
})

test("raw upload preserves explicit content type and bytes", async ({
  page,
}) => {
  await signIn(page)
  await page.route("**/api/v1/documents?*", (route) =>
    route.fulfill({ status: 202, json: { ...document, state: "queued" } }),
  )
  const upload = page.waitForRequest("**/api/v1/documents?*")
  const sending = page.evaluate(async () => {
    const modulePath = "/src/api/documents.ts"
    const { documentsApi } = await import(modulePath)
    await documentsApi.upload(
      "test-only-token",
      new File(["plain bytes"], "a & b.txt"),
    )
  })
  const result = await Promise.all([upload, sending])
  expect(result[0].headers()["content-type"]).toBe("application/octet-stream")
  expect(result[0].postData()).toBe("plain bytes")
  expect(new URL(result[0].url()).searchParams.get("filename")).toBe(
    "a & b.txt",
  )
})
