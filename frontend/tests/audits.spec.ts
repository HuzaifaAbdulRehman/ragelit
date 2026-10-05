import { readFile } from "node:fs/promises"
import { expect, test } from "@playwright/test"
import { evidence, openAudit, summary } from "./helpers/audits"
import { mockSession, runId, signIn } from "./helpers/session"

for (const role of ["owner", "admin", "auditor", "member"] as const) {
  test(`${role} audit navigation`, async ({ page }) => {
    await mockSession(page, role)
    await signIn(page)
    await expect(
      page.getByRole("link", { name: "Audits", exact: true }),
    ).toHaveCount(role === "member" ? 0 : 1)
  })
}
test("start queues a run then renders stages and downloads original bytes", async ({
  page,
}) => {
  await mockSession(page)
  let running = false
  await page.route("**/api/v1/audits?*", (route) =>
    route.fulfill({ json: { items: [], total: 0, limit: 20, offset: 0 } }),
  )
  await page.route("**/api/v1/audits", (route) => {
    expect(route.request().method()).toBe("POST")
    expect(route.request().postDataJSON()).toEqual({})
    return route.fulfill({ status: 202, json: summary })
  })
  const report = {
    ...evidence,
    results: [
      {
        ...evidence.results[0],
        case_id: "<script data-audit>window.auditExecuted=1</script>",
      },
    ],
  }
  await page.route(`**/api/v1/audits/${runId}`, (route) =>
    route.fulfill({
      json: running
        ? {
            ...summary,
            state: "finished",
            outcome: "inconclusive",
            exit_code: 2,
            report_available: true,
            report_id: report.run_id,
            report,
          }
        : { ...summary, state: "running", report: null },
    }),
  )
  const original = Buffer.from(
    '{ "synthetic": "café", "exit_code": 2 }\n',
    "utf8",
  )
  await page.route(`**/api/v1/audits/${runId}/report.json`, (route) =>
    route.fulfill({ body: original, contentType: "application/json" }),
  )
  await signIn(page)
  await page.getByRole("link", { name: "Audits", exact: true }).click()
  await page.getByRole("button", { name: "Start synthetic audit" }).click()
  await expect(page.getByTestId("audit-state")).toHaveText("running")
  running = true
  await expect(page.getByTestId("audit-outcome")).toHaveText("inconclusive")
  await expect(
    page.getByText(report.results[0].case_id, { exact: false }),
  ).toBeVisible()
  await page.getByText(report.results[0].case_id, { exact: false }).click()
  await expect(
    page.getByText("output_candidate", { exact: true }),
  ).toBeVisible()
  await expect(page.getByText("not_reached", { exact: true })).toBeVisible()
  await expect(page.getByText("unobserved", { exact: true })).toBeVisible()
  await expect(page.locator("script[data-audit]")).toHaveCount(0)
  expect(await page.evaluate(() => "auditExecuted" in window)).toBe(false)
  const downloaded = page.waitForEvent("download")
  await page.getByRole("button", { name: "Download JSON" }).click()
  const download = await downloaded
  const savedPath = await download.path()
  if (!savedPath) throw new Error("Download was not saved")
  expect(await readFile(savedPath)).toEqual(original)
})
for (const [exit, outcome] of [
  [0, "pass"],
  [1, "fail"],
  [2, "inconclusive"],
] as const) {
  test(`exit ${exit} displays ${outcome}`, async ({ page }) => {
    await mockSession(page)
    await page.route(`**/api/v1/audits/${runId}`, (route) =>
      route.fulfill({
        json: {
          ...summary,
          state: "finished",
          exit_code: exit,
          outcome,
          report: null,
        },
      }),
    )
    await openAudit(page)
    await expect(page.getByTestId("audit-outcome")).toHaveText(outcome)
    await expect(
      page.getByText("No validated report is available."),
    ).toBeVisible()
  })
}
test("recovery required stops polling and gives operator guidance", async ({
  page,
}) => {
  await mockSession(page)
  let requests = 0
  await page.route(`**/api/v1/audits/${runId}`, (route) => {
    requests++
    return route.fulfill({
      json: {
        ...summary,
        state: "recovery_required",
        outcome: "inconclusive",
        exit_code: 2,
        error_code: "audit_interrupted",
        report: null,
      },
    })
  })
  await openAudit(page)
  await expect(
    page.getByText(
      "Confirm the worker and its child have stopped, then use the operator recovery command.",
    ),
  ).toBeVisible()
  await page.waitForTimeout(2200)
  expect(requests).toBe(1)
})
