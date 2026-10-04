import { expect, test } from "@playwright/test"
import { evidence, openAudit, summary } from "./helpers/audits"
import { mockSession, problem, runId } from "./helpers/session"

test("late old-workspace evidence is discarded after switch", async ({
  page,
}) => {
  await mockSession(page)
  let release!: () => void
  let started!: () => void
  const waiting = new Promise<void>((resolve) => {
    started = resolve
  })
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route(`**/api/v1/audits/${runId}`, async (route) => {
    started()
    await gate
    await route
      .fulfill({ json: { ...summary, report: evidence } })
      .catch(() => {})
  })
  await openAudit(page)
  await waiting
  await page
    .getByLabel("Organization switcher")
    .selectOption({ label: "Harbor Works" })
  await expect(
    page.getByRole("heading", { name: "Harbor Works" }),
  ).toBeVisible()
  release()
  await expect(page.getByText("synthetic-case", { exact: false })).toHaveCount(
    0,
  )
})
for (const status of [401, 403]) {
  test(`denial ${status} clears cached evidence and stops polling`, async ({
    page,
  }) => {
    await mockSession(page)
    let requests = 0
    await page.route(`**/api/v1/audits/${runId}`, (route) => {
      requests++
      return requests === 1
        ? route.fulfill({
            json: { ...summary, state: "running", report: evidence },
          })
        : route.fulfill({
            status,
            json: problem("action_forbidden", status, "Access denied."),
          })
    })
    await openAudit(page)
    await expect(
      page.getByText("synthetic-case", { exact: false }),
    ).toBeVisible()
    await expect(
      page.getByText("synthetic-case", { exact: false }),
    ).toHaveCount(0)
    await page.waitForTimeout(2200)
    expect(requests).toBe(2)
  })
}
