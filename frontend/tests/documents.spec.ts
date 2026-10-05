import { closeSync, ftruncateSync, openSync } from "node:fs"
import { expect, test } from "@playwright/test"
import {
  document,
  documentId,
  mockSession,
  problem,
  signIn,
} from "./helpers/session"

test("upload raw bytes queues a restricted document and polls until ready", async ({
  page,
}) => {
  await mockSession(page)
  let created = false
  let reads = 0
  let uploads = 0
  await page.route("**/api/v1/documents**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (request.method() === "POST") {
      expect(request.headers()["content-type"]).toBe("application/octet-stream")
      expect(request.postData()).toBe("Synthetic handbook evidence")
      expect(url.searchParams.get("filename")).toBe("notes & policy.txt")
      created = true
      uploads++
      await route.fulfill({
        status: 202,
        json: { ...document, filename: "notes & policy.txt", state: "queued" },
      })
    } else if (url.pathname.endsWith("/versions")) {
      await route.fulfill({ json: [] })
    } else if (url.pathname.endsWith(documentId)) {
      reads++
      await route.fulfill({
        json: {
          ...document,
          filename: "notes & policy.txt",
          state: reads === 1 ? "queued" : reads === 2 ? "processing" : "ready",
        },
      })
    } else {
      await route.fulfill({
        json: created ? [{ ...document, filename: "notes & policy.txt" }] : [],
      })
    }
  })
  await signIn(page)
  await page.getByRole("link", { name: "Documents", exact: true }).click()
  await page.getByLabel("Document file").setInputFiles({
    name: "notes & policy.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Synthetic handbook evidence"),
  })
  await page
    .getByRole("button", { name: "Upload document", exact: true })
    .click()
  await expect(page.getByRole("status")).toContainText("Queued")
  await expect(page.getByRole("status")).toContainText("Ready", {
    timeout: 10000,
  })
  await page.getByRole("link", { name: "Documents", exact: true }).click()
  await page.getByLabel("Document file").setInputFiles({
    name: "notes & policy.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Synthetic handbook evidence"),
  })
  await page
    .getByRole("button", { name: "Upload document", exact: true })
    .click()
  await expect(page).toHaveURL(new RegExp(`/documents/${documentId}$`))
  expect(uploads).toBe(2)
  await page.getByRole("link", { name: "Documents", exact: true }).click()
  await expect(
    page.getByRole("link", { name: "notes & policy.txt", exact: true }),
  ).toHaveCount(1)
})

test("unsupported and oversized files are rejected before upload", async ({
  page,
}, testInfo) => {
  await mockSession(page)
  await page.route("**/api/v1/documents?*", (route) =>
    route.fulfill({ json: [] }),
  )
  let attempted = 0
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/documents"))
      attempted++
  })
  await signIn(page)
  await page.getByRole("link", { name: "Documents", exact: true }).click()
  await page.getByLabel("Document file").setInputFiles({
    name: "program.exe",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("invalid"),
  })
  await page
    .getByRole("button", { name: "Upload document", exact: true })
    .click()
  await expect(page.getByRole("alert")).toContainText("TXT, Markdown, DOCX")
  const largeFile = testInfo.outputPath("large.txt")
  const descriptor = openSync(largeFile, "w")
  try {
    ftruncateSync(descriptor, 25 * 1024 * 1024 + 1)
  } finally {
    closeSync(descriptor)
  }
  await page.getByLabel("Document file").setInputFiles(largeFile)
  await page
    .getByRole("button", { name: "Upload document", exact: true })
    .click()
  await expect(page.getByRole("alert")).toContainText("25 MiB")
  expect(attempted).toBe(0)
})

test("failed indexing can retry and replace versions without false success", async ({
  page,
}) => {
  await mockSession(page)
  let state = "failed"
  let versionCount = 1
  await page.route("**/api/v1/documents**", async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith("/retry")) {
      await route.fulfill({
        status: 503,
        json: problem(
          "projection_unavailable",
          503,
          "Indexing service unavailable.",
        ),
      })
    } else if (path.endsWith("/versions") && request.method() === "POST") {
      expect(request.postData()).toBe("Replacement evidence")
      versionCount++
      state = "queued"
      await route.fulfill({ status: 202, json: { ...document, state } })
    } else if (path.endsWith("/versions")) {
      await route.fulfill({
        json: Array.from({ length: versionCount }, (_, index) => ({
          id: `version-${index}`,
          state: index === 0 ? "failed" : "queued",
          chunk_count: 0,
          created_at: document.created_at,
        })),
      })
    } else if (path.endsWith(documentId)) {
      await route.fulfill({ json: { ...document, state } })
    } else {
      await route.fulfill({ json: [{ ...document, state }] })
    }
  })
  await signIn(page)
  await page.getByRole("link", { name: "Documents", exact: true }).click()
  await page.getByRole("link", { name: document.filename, exact: true }).click()
  await expect(
    page.getByRole("button", { name: "Retry indexing" }),
  ).toBeVisible()
  await page.getByRole("button", { name: "Retry indexing" }).click()
  await expect(page.getByRole("alert")).toContainText(
    "Indexing service unavailable",
  )
  await expect(page.getByRole("status")).toContainText("Failed")
  await page.getByLabel("Replacement file").setInputFiles({
    name: "replacement.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Replacement evidence"),
  })
  await page.getByRole("button", { name: "Replace version" }).click()
  await expect(page.getByRole("status")).toContainText("Queued")
  await expect(
    page.getByRole("list", { name: "Version history" }).getByRole("listitem"),
  ).toHaveCount(2)
})

test("document pages render hostile names as text and confirm deletion", async ({
  page,
}) => {
  await mockSession(page)
  let removed = false
  const hostile = "<img src=x onerror=alert(1)>.txt"
  await page.route("**/api/v1/documents**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (request.method() === "DELETE") {
      removed = true
      await route.fulfill({ status: 204 })
    } else if (url.pathname.endsWith("/versions"))
      await route.fulfill({ json: [] })
    else if (url.pathname.endsWith(documentId))
      await route.fulfill({ json: { ...document, filename: hostile } })
    else {
      const offset = Number(url.searchParams.get("offset") ?? 0)
      await route.fulfill({
        json: removed
          ? []
          : offset === 0
            ? Array.from({ length: 50 }, (_, i) => ({
                ...document,
                id: i === 0 ? documentId : `doc-${i}`,
                filename: i === 0 ? hostile : `note-${i}.txt`,
              }))
            : [{ ...document, filename: "last.txt" }],
      })
    }
  })
  await signIn(page)
  await page.getByRole("link", { name: "Documents", exact: true }).click()
  await expect(page.getByText(hostile, { exact: true })).toBeVisible()
  await expect(page.locator("img[src=x]")).toHaveCount(0)
  await page.getByRole("button", { name: "Next documents" }).click()
  await expect(
    page.getByRole("link", { name: "last.txt", exact: true }),
  ).toBeVisible()
  await page.getByRole("button", { name: "Previous documents" }).click()
  await page.getByRole("link", { name: hostile, exact: true }).click()
  page.once("dialog", (dialog) => dialog.dismiss())
  await page
    .getByRole("button", { name: "Delete document", exact: true })
    .click()
  expect(removed).toBe(false)
  page.once("dialog", (dialog) => dialog.accept())
  await page
    .getByRole("button", { name: "Delete document", exact: true })
    .click()
  await expect(page.getByText("No documents on this page.")).toBeVisible()
  expect(removed).toBe(true)
})

test("members get documents without upload controls", async ({ page }) => {
  await mockSession(page, "member")
  await page.route("**/api/v1/documents?*", (route) =>
    route.fulfill({ json: [] }),
  )
  await signIn(page)
  await page.getByRole("link", { name: "Documents", exact: true }).click()
  await expect(
    page.getByRole("heading", { name: "Documents", exact: true }),
  ).toBeVisible()
  await expect(page.getByLabel("Document file")).toHaveCount(0)
})

test("leaving the upload page cancels its pending request", async ({
  page,
}) => {
  await mockSession(page)
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  let aborted = false
  page.on("requestfailed", (request) => {
    if (request.method() === "POST" && request.url().includes("/documents"))
      aborted = true
  })
  await page.route("**/api/v1/documents**", async (route) => {
    if (route.request().method() === "POST") {
      await gate
      await route.fulfill({
        status: 202,
        json: { ...document, state: "queued" },
      })
    } else await route.fulfill({ json: [] })
  })
  await signIn(page)
  await page.getByRole("link", { name: "Documents", exact: true }).click()
  await page.getByLabel("Document file").setInputFiles({
    name: "pending.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Pending evidence"),
  })
  const request = page.waitForRequest(
    (request) =>
      request.method() === "POST" && request.url().includes("/documents"),
  )
  await page
    .getByRole("button", { name: "Upload document", exact: true })
    .click()
  await request
  await page.getByRole("link", { name: "People", exact: true }).click()
  try {
    await expect.poll(() => aborted).toBe(true)
  } finally {
    release()
  }
  await expect(
    page.getByRole("heading", { name: "People", exact: true }),
  ).toBeVisible()
})
