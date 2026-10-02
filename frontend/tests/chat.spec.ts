import { expect, type Page, test } from "@playwright/test"
import type { components } from "../src/api/generated/schema"
import {
  documentId,
  mockSession,
  problem,
  runId,
  signIn,
} from "./helpers/session"

const chunkId = "88888888-8888-4888-8888-888888888888"
const answer: components["schemas"]["AnswerResponse"] = {
  query_run_id: runId,
  status: "answered",
  answer: "The policy permits twenty days.",
  citations: [
    {
      chunk_id: chunkId,
      document_id: documentId,
      version_id: "99999999-9999-4999-8999-999999999999",
      filename: "handbook.txt",
      location: "paragraph 1",
    },
  ],
}
const trace: components["schemas"]["TraceResponse"] = {
  id: runId,
  state: "completed",
  error_code: null,
  created_at: "2026-10-02T10:00:00Z",
  stages: [
    {
      stage: "retrieval",
      decision: "allowed",
      duration_ms: 3,
      chunk_ids: [chunkId],
    },
    {
      stage: "context",
      decision: "bounded",
      duration_ms: 1,
      chunk_ids: [chunkId],
    },
  ],
}
async function openChat(page: Page) {
  await mockSession(page)
  await page.route("**/api/v1/query-runs/*", (route) =>
    route.fulfill({ json: trace }),
  )
  await signIn(page)
  await page.getByRole("link", { name: "Chat", exact: true }).click()
}
async function ask(page: Page, question = "What is the leave policy?") {
  await page.getByLabel("Your question", { exact: true }).fill(question)
  await page.getByRole("button", { name: "Ask question", exact: true }).click()
}

test("trimmed questions use limit ten and return cited evidence and trace", async ({
  page,
}) => {
  await page.route("**/api/v1/chat/query", (route) => {
    expect(route.request().postDataJSON()).toEqual({
      question: "What is the leave policy?",
      limit: 10,
    })
    return route.fulfill({ json: answer })
  })
  await openChat(page)
  await ask(page, "  What is the leave policy?  ")
  await expect(
    page.getByText(answer.answer ?? "", { exact: true }),
  ).toBeVisible()
  await expect(page.getByRole("list", { name: "Citations" })).toContainText(
    "handbook.txt",
  )
  await expect(page.getByRole("list", { name: "Citations" })).toContainText(
    "paragraph 1",
  )
  await page
    .getByRole("button", { name: "View query trace", exact: true })
    .click()
  await expect(page.getByRole("region", { name: "Query trace" })).toContainText(
    "retrieval",
  )
  await expect(page.getByRole("region", { name: "Query trace" })).toContainText(
    chunkId,
  )
  await expect(page.getByText("Accuracy", { exact: true })).toHaveCount(0)
})

test("missing evidence abstains without implying a forbidden document", async ({
  page,
}) => {
  await page.route("**/api/v1/chat/query", (route) =>
    route.fulfill({
      json: { ...answer, status: "abstained", answer: null, citations: [] },
    }),
  )
  await openChat(page)
  await ask(page)
  await expect(
    page.getByText("No supporting evidence was found", { exact: true }),
  ).toBeVisible()
  await expect(page.getByRole("list", { name: "Citations" })).toHaveCount(0)
  await expect(
    page.getByText("forbidden document", { exact: false }),
  ).toHaveCount(0)
})

for (const [status, code] of [
  [502, "generation_unavailable"],
  [504, "generation_timeout"],
  [503, "generation_not_configured"],
] as const) {
  test(`${code} preserves retrieval trace and offers recovery`, async ({
    page,
  }) => {
    await page.route("**/api/v1/chat/query", (route) =>
      route.fulfill({
        status,
        json: {
          ...problem(
            code,
            status,
            "The query could not be completed. Its trace is available.",
          ),
          query_run_id: runId,
        },
      }),
    )
    await openChat(page)
    await ask(page)
    await expect(page.getByRole("alert")).toBeVisible()
    if (code === "generation_not_configured") {
      await expect(page.getByRole("alert")).toContainText(
        "server-side LLM endpoint",
      )
      await expect(page.getByLabel("API key", { exact: false })).toHaveCount(0)
    }
    await expect(
      page.getByRole("button", { name: "View query trace", exact: true }),
    ).toBeEnabled()
    await page
      .getByRole("button", { name: "View query trace", exact: true })
      .click()
    await expect(
      page.getByRole("region", { name: "Query trace" }),
    ).toContainText("context")
    await expect(
      page.getByRole("button", { name: "Retry question", exact: true }),
    ).toBeEnabled()
  })
}

test("hostile model text remains text rather than executable markup", async ({
  page,
}) => {
  const hostile =
    "<script data-answer>alert(1)</script><img src=x onerror=alert(2)>"
  await page.route("**/api/v1/chat/query", (route) =>
    route.fulfill({ json: { ...answer, answer: hostile } }),
  )
  await openChat(page)
  await ask(page)
  await expect(page.getByText(hostile, { exact: true })).toBeVisible()
  await expect(page.locator("script[data-answer]")).toHaveCount(0)
  await expect(page.locator("img[src=x]")).toHaveCount(0)
})

test("unauthorized traces show no private identifiers", async ({ page }) => {
  await page.route("**/api/v1/chat/query", (route) =>
    route.fulfill({ json: answer }),
  )
  await openChat(page)
  await page.route("**/api/v1/query-runs/*", (route) =>
    route.fulfill({
      status: 404,
      json: problem(
        "resource_not_found",
        404,
        "The requested resource was not found.",
      ),
    }),
  )
  await ask(page)
  await page
    .getByRole("button", { name: "View query trace", exact: true })
    .click()
  await expect(page.getByRole("alert")).toContainText("not found")
  await expect(page.getByText(chunkId, { exact: true })).toHaveCount(0)
})

test("malformed run identifiers do not become trace requests", async ({
  page,
}) => {
  await page.route("**/api/v1/chat/query", (route) =>
    route.fulfill({
      status: 502,
      json: {
        ...problem("generation_unavailable", 502, "Generation failed."),
        query_run_id: "../other-resource",
      },
    }),
  )
  await openChat(page)
  await ask(page)
  await expect(page.getByRole("alert")).toBeVisible()
  await expect(
    page.getByRole("button", { name: "View query trace", exact: true }),
  ).toHaveCount(0)
})

test("cancelled queries can retry without a stale answer", async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  let requests = 0
  await page.route("**/api/v1/chat/query", async (route) => {
    requests++
    const attempt = requests
    if (attempt === 1) await gate
    await route.fulfill({
      json:
        attempt === 1
          ? { ...answer, answer: "Stale cancelled answer" }
          : answer,
    })
  })
  await openChat(page)
  await ask(page)
  await page
    .getByRole("button", { name: "Cancel question", exact: true })
    .click()
  await expect(
    page.getByText("Request cancelled.", { exact: true }),
  ).toBeVisible()
  release()
  await page
    .getByRole("button", { name: "Retry question", exact: true })
    .click()
  await expect(
    page.getByText(answer.answer ?? "", { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText("Stale cancelled answer", { exact: true }),
  ).toHaveCount(0)
})

test("an old query cannot populate a new organization", async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route("**/api/v1/chat/query", async (route) => {
    if (route.request().postDataJSON().question === "Old question") await gate
    await route.fulfill({
      json: {
        ...answer,
        answer:
          route.request().postDataJSON().question === "Old question"
            ? "Old workspace secret"
            : "New workspace answer",
      },
    })
  })
  await openChat(page)
  const request = page.waitForRequest("**/api/v1/chat/query")
  await ask(page, "Old question")
  await request
  await page
    .getByLabel("Organization switcher")
    .selectOption({ label: "Harbor Works" })
  await expect(
    page.getByRole("heading", { name: "Harbor Works", exact: true }),
  ).toBeVisible()
  release()
  await page.getByRole("link", { name: "Chat", exact: true }).click()
  await ask(page, "New question")
  await expect(
    page.getByText("New workspace answer", { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText("Old workspace secret", { exact: true }),
  ).toHaveCount(0)
})
