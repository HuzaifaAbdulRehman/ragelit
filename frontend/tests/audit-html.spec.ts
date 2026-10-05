import { spawnSync } from "node:child_process"
import { readFile, writeFile } from "node:fs/promises"
import path from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"
import { expect, test } from "@playwright/test"
import { evidence, openAudit, summary } from "./helpers/audits"
import { mockSession, runId } from "./helpers/session"

test("HTML download keeps the report name and original export bytes", async ({
  page,
}) => {
  await mockSession(page)
  await page.route(`**/api/v1/audits/${runId}`, (route) =>
    route.fulfill({
      json: {
        ...summary,
        state: "finished",
        outcome: "inconclusive",
        exit_code: 2,
        report_available: true,
        report_id: evidence.run_id,
        report: evidence,
      },
    }),
  )
  const content = Buffer.from(
    "<!doctype html><title>Synthetic report</title><p>Outcome: inconclusive</p>",
  )
  await page.route(`**/api/v1/audits/${runId}/report.html`, (route) =>
    route.fulfill({ body: content, contentType: "text/html" }),
  )
  await openAudit(page)
  const downloaded = page.waitForEvent("download")
  await page.getByRole("button", { name: "Download HTML" }).click()
  const download = await downloaded
  expect(download.suggestedFilename()).toBe(`${evidence.run_id}.html`)
  const saved = await download.path()
  if (!saved) throw new Error("HTML export was not saved")
  expect(await readFile(saved)).toEqual(content)
})

test("real HTML renderer displays hostile values offline without executing them", async ({
  page,
}, testInfo) => {
  const hostile =
    '<script>window.auditExecuted=1</script><img src="https://example.invalid/x" onerror="window.auditExecuted=2">'
  const report = { ...evidence, required_case_ids: [hostile] }
  const frontend = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
  const backend = path.resolve(frontend, "../backend")
  const python = path.join(
    backend,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  )
  const rendered = spawnSync(
    python,
    [
      "-c",
      "import sys; from app.audits.html import render_html; from app.audits.reports import AuditReport; print(render_html(AuditReport.model_validate_json(sys.stdin.read())))",
    ],
    {
      cwd: backend,
      input: JSON.stringify(report),
      encoding: "utf8",
      windowsHide: true,
      timeout: 30_000,
    },
  )
  expect(rendered.status).toBe(0)
  expect(rendered.stderr).toBe("")
  const destination = testInfo.outputPath("hostile-report.html")
  await writeFile(destination, rendered.stdout)
  const outbound: string[] = []
  page.on("request", (request) => {
    if (/^https?:/.test(request.url())) outbound.push(request.url())
  })
  await page.goto(pathToFileURL(destination).href)
  await expect(page.getByText(hostile, { exact: true })).toBeVisible()
  await expect(
    page.getByText("Outcome: inconclusive", { exact: true }),
  ).toBeVisible()
  await expect(page.locator("script, img, iframe, object, embed")).toHaveCount(
    0,
  )
  expect(await page.evaluate(() => "auditExecuted" in window)).toBe(false)
  expect(outbound).toEqual([])
  await page.screenshot({
    path: testInfo.outputPath("offline-report.png"),
    fullPage: true,
  })
})
