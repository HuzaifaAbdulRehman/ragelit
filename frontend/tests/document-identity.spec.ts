import { expect, type Page, test } from "@playwright/test"
import { document, mockSession, signIn } from "./helpers/session"

const target = { ...document, filename: "public-policy.txt" }
const origin = {
  ...document,
  id: "88888888-8888-4888-8888-888888888888",
  filename: "private-research.txt",
  state: "failed",
}

async function visitBothDocuments(page: Page) {
  await signIn(page)
  await page.getByRole("link", { name: "Documents", exact: true }).click()
  await page.getByRole("link", { name: target.filename, exact: true }).click()
  await expect(
    page.getByRole("heading", { name: target.filename, exact: true }),
  ).toBeVisible()
  await page.getByRole("link", { name: "Documents", exact: true }).click()
  await page.getByRole("link", { name: origin.filename, exact: true }).click()
  await expect(
    page.getByRole("heading", { name: origin.filename, exact: true }),
  ).toBeVisible()
}

async function jumpToCachedTarget(page: Page) {
  await page.evaluate(() => window.history.go(-2))
  await expect(page).toHaveURL(new RegExp(`/documents/${target.id}$`))
  await expect(
    page.getByRole("heading", { name: target.filename, exact: true }),
  ).toBeVisible()
}

test("a cached history jump discards the previous document's replacement file", async ({
  page,
}) => {
  await mockSession(page)
  await page.route("**/api/v1/documents**", async (route) => {
    const path = new URL(route.request().url()).pathname
    await route.fulfill({
      json: path.endsWith("/versions")
        ? []
        : path.endsWith(target.id)
          ? target
          : path.endsWith(origin.id)
            ? origin
            : [target, origin],
    })
  })
  await visitBothDocuments(page)
  await page.getByLabel("Replacement file").setInputFiles({
    name: "private-replacement.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Private replacement evidence"),
  })
  await jumpToCachedTarget(page)
  await expect(page.getByLabel("Replacement file")).toHaveValue("")
  await page
    .getByRole("button", { name: "Replace version", exact: true })
    .click()
  expect(
    await page
      .getByLabel("Replacement file")
      .evaluate((input: HTMLInputElement) => input.validity.valueMissing),
  ).toBe(true)
})

for (const kind of ["retry", "replace", "delete"] as const) {
  test(`a cached history jump cancels a pending document ${kind}`, async ({
    page,
  }) => {
    await mockSession(page)
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    let delivered!: () => void
    const responseDelivered = new Promise<void>((resolve) => {
      delivered = resolve
    })
    let aborted = false
    page.on("requestfailed", (request) => {
      if (
        request.method() !== "GET" &&
        request.url().includes(`/documents/${origin.id}`)
      )
        aborted = true
    })
    await page.route("**/api/v1/documents**", async (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      if (request.method() !== "GET") {
        expect(path).toBe(
          `/api/v1/documents/${origin.id}${kind === "retry" ? "/retry" : kind === "replace" ? "/versions" : ""}`,
        )
        await gate
        try {
          await route.fulfill(
            kind === "delete"
              ? { status: 204 }
              : { status: 202, json: { ...origin, state: "queued" } },
          )
        } finally {
          delivered()
        }
      } else
        await route.fulfill({
          json: path.endsWith("/versions")
            ? []
            : path.endsWith(target.id)
              ? target
              : path.endsWith(origin.id)
                ? origin
                : [target, origin],
        })
    })
    await visitBothDocuments(page)
    if (kind === "replace") {
      await page.getByLabel("Replacement file").setInputFiles({
        name: "private-replacement.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("Private replacement evidence"),
      })
    }
    if (kind === "delete") page.once("dialog", (dialog) => dialog.accept())
    const started = page.waitForRequest(
      (request) =>
        request.method() !== "GET" &&
        request.url().includes(`/documents/${origin.id}`),
    )
    await page
      .getByRole("button", {
        name:
          kind === "retry"
            ? "Retry indexing"
            : kind === "replace"
              ? "Replace version"
              : "Delete document",
        exact: true,
      })
      .click()
    await started
    await jumpToCachedTarget(page)
    try {
      await expect.poll(() => aborted).toBe(true)
    } finally {
      release()
    }
    await responseDelivered
    await expect(page).toHaveURL(new RegExp(`/documents/${target.id}$`))
    await expect(
      page.getByRole("heading", { name: target.filename, exact: true }),
    ).toBeVisible()
    await expect(page.getByRole("status")).toContainText("Ready")
    await expect(page.getByLabel("Replacement file")).toHaveValue("")
  })
}
