import { spawn, spawnSync } from "node:child_process"
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"

const frontendDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const backendDir = path.resolve(frontendDir, "../backend")
const dataDir = mkdtempSync(path.join(tmpdir(), "ragelit-e2e-"))
const environment = {
  ...Object.fromEntries(
    Object.entries(process.env).filter(
      ([key]) => !key.startsWith("RAGELIT_AUDIT_"),
    ),
  ),
  RAGELIT_ENVIRONMENT: "test",
  RAGELIT_SECRET_KEY: "e2e-secret-that-is-at-least-32-bytes",
  RAGELIT_DATABASE_ADMIN_URL:
    "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/ragelit_e2e",
  RAGELIT_DATABASE_URL:
    "postgresql+psycopg://ragelit_app:ragelit_app@127.0.0.1:5432/ragelit_e2e",
  RAGELIT_QDRANT_URL: "http://127.0.0.1:6333",
  RAGELIT_COOKIE_SECURE: "false",
  RAGELIT_QDRANT_COLLECTION: "ragelit_e2e",
  RAGELIT_DATA_DIR: dataDir,
  RAGELIT_LLM_BASE_URL: "http://127.0.0.1:9/v1",
  RAGELIT_LLM_MODEL: "",
  RAGELIT_LLM_API_KEY: "",
}

const seeded = spawnSync("uv", ["run", "python", "-m", "tests.e2e_seed"], {
  cwd: backendDir,
  env: environment,
  stdio: "inherit",
  windowsHide: true,
})
function removeData() {
  if (
    path.dirname(dataDir) !== path.resolve(tmpdir()) ||
    !path.basename(dataDir).startsWith("ragelit-e2e-")
  )
    throw new Error("Unsafe fixture cleanup target")
  rmSync(dataDir, { recursive: true, force: true })
}
if (seeded.status !== 0) {
  removeData()
  process.exit(seeded.status ?? 1)
}

const python = path.join(
  backendDir,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
)
const options = {
  cwd: backendDir,
  env: environment,
  stdio: "inherit",
  windowsHide: true,
}

const api = spawn(
  python,
  [
    "-m",
    "uvicorn",
    "tests.e2e_app:create_app",
    "--factory",
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
  ],
  options,
)
const worker = spawn(python, ["-m", "tests.e2e_worker"], options)
let stopping = false
function stop(code) {
  if (stopping) return
  stopping = true
  for (const child of [api, worker]) {
    if (!child.pid || child.exitCode !== null) continue
    if (process.platform === "win32") {
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
        windowsHide: true,
        stdio: "ignore",
      })
    } else {
      try {
        process.kill(child.pid, "SIGKILL")
      } catch (error) {
        if (error.code !== "ESRCH") throw error
      }
    }
  }
  removeData()
  process.exit(code)
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => stop(0))
}
for (const child of [api, worker]) {
  child.on("error", () => stop(1))
  child.on("exit", (code) => stop(code || 1))
}
