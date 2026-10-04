import { type ChildProcess, spawn, spawnSync } from "node:child_process"
import { createHash, randomUUID } from "node:crypto"
import { once } from "node:events"
import { mkdirSync } from "node:fs"
import { readFile } from "node:fs/promises"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { expect, type Response, test } from "@playwright/test"
import type { components } from "../src/api/generated/schema"
import { signIn } from "./helpers/session"

async function stopOwnedWorker(worker: ChildProcess) {
  if (worker.exitCode !== null || !worker.pid) return
  const closed = once(worker, "close")
  if (process.platform === "win32") {
    const stopped = spawnSync(
      "taskkill",
      ["/PID", String(worker.pid), "/T", "/F"],
      {
        windowsHide: true,
        stdio: "ignore",
        timeout: 10_000,
      },
    )
    if (stopped.status !== 0)
      throw new Error("Owned audit worker cleanup failed")
  } else process.kill(-worker.pid, "SIGKILL")
  await Promise.race([
    closed,
    new Promise((_, reject) =>
      setTimeout(
        () => reject(new Error("Owned audit worker did not close")),
        10_000,
      ),
    ),
  ])
}

test("queued job matches real safe CLI artifact", async ({ page }) => {
  test.setTimeout(1_900_000)
  await signIn(page)
  const organizationId = await page
    .getByLabel("Organization switcher")
    .inputValue()
  await page.getByRole("link", { name: "Audits", exact: true }).click()
  const queued = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/audits") &&
      response.request().method() === "POST",
  )
  await page.getByRole("button", { name: "Start synthetic audit" }).click()
  const response = await queued
  expect(response.status()).toBe(202)
  const run =
    (await response.json()) as components["schemas"]["AuditRunSummary"]
  expect(run.state).toBe("queued")
  const frontend = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
  const backend = path.resolve(frontend, "../backend")
  const root = path.resolve(
    frontend,
    `../data/audit-workspaces/dashboard-e2e-${randomUUID()}`,
  )
  mkdirSync(root, { recursive: true })
  const essentials = new Set([
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "PATHEXT",
    "COMSPEC",
  ])
  const environment = {
    ...Object.fromEntries(
      Object.entries(process.env).filter(([key]) =>
        essentials.has(key.toUpperCase()),
      ),
    ),
    RAGELIT_DATABASE_URL:
      "postgresql+psycopg://ragelit_app:ragelit_app@127.0.0.1:5432/ragelit_e2e",
    RAGELIT_AUDIT_ENVIRONMENT: "test",
    RAGELIT_AUDIT_DATABASE_ADMIN_URL:
      "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres",
    RAGELIT_AUDIT_QDRANT_URL: "http://127.0.0.1:6333",
    RAGELIT_AUDIT_ROOT: root,
    RAGELIT_AUDIT_APPLICATION_PASSWORD: randomUUID().replaceAll("-", ""),
    RAGELIT_AUDIT_FIXTURE_PASSWORD: randomUUID().replaceAll("-", ""),
  }
  const python = path.join(
    backend,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  )
  const worker = spawn(
    python,
    ["-m", "app.workers.audit", "--organization-id", organizationId, "--once"],
    {
      cwd: backend,
      env: environment,
      stdio: "ignore",
      windowsHide: true,
      detached: process.platform !== "win32",
    },
  )
  const finished = page.waitForResponse(
    async (current) => {
      if (!current.url().endsWith(`/api/v1/audits/${run.id}`) || !current.ok())
        return false
      const state = (
        (await current.json()) as components["schemas"]["AuditRunDetail"]
      ).state
      return state === "finished" || state === "recovery_required"
    },
    { timeout: 1_850_000 },
  )
  const exited = new Promise<void>((resolve, reject) => {
    worker.once("error", () =>
      reject(new Error("Audit worker could not start")),
    )
    worker.once("exit", (code) =>
      code === 0 ? resolve() : reject(new Error("Audit worker failed")),
    )
  })
  let finalResponse: Response | undefined
  try {
    const [result] = await Promise.all([finished, exited])
    finalResponse = result
  } finally {
    await stopOwnedWorker(worker)
  }
  if (!finalResponse) throw new Error("Audit result was not returned")
  const detail =
    (await finalResponse.json()) as components["schemas"]["AuditRunDetail"]
  expect(detail.outcome).toBe("pass")
  expect(detail.exit_code).toBe(0)
  expect(detail.report?.metadata.profile).toBe("safe")
  expect(detail.report?.coverage_complete).toBe(true)
  expect(detail.report?.results).toHaveLength(51)
  expect(detail.report_id).not.toBe(run.id)
  const artifact = path.join(
    root,
    `ragelit_audit_${run.id.replaceAll("-", "")}`,
    "reports",
    `${detail.report_id}.json`,
  )
  const original = await readFile(artifact)
  const digest = createHash("sha256").update(original).digest("hex")
  expect(digest).toBe(detail.report_sha256)
  const receipt = JSON.parse(
    await readFile(artifact.replace(/\.json$/, ".sha256.json"), "utf8"),
  )
  expect(receipt.sha256).toBe(digest)
  await expect(page.getByTestId("audit-outcome")).toHaveText("pass")
  const downloadEvent = page.waitForEvent("download")
  await page.getByRole("button", { name: "Download JSON" }).click()
  const download = await downloadEvent
  const savedPath = await download.path()
  if (!savedPath) throw new Error("Download was not saved")
  expect((await readFile(savedPath)).equals(original)).toBe(true)
  await page.screenshot({
    path: path.join(root, "dashboard-desktop.png"),
    fullPage: true,
  })
  console.info(
    JSON.stringify({
      request_id: run.id,
      report_id: detail.report_id,
      sha256: digest,
      artifact,
    }),
  )
})
