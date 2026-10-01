import { spawn, spawnSync } from "node:child_process"
import path from "node:path"
import { fileURLToPath } from "node:url"

const frontendDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const backendDir = path.resolve(frontendDir, "../backend")
const environment = {
  ...process.env,
  RAGELIT_ENVIRONMENT: "test",
  RAGELIT_SECRET_KEY: "e2e-secret-that-is-at-least-32-bytes",
  RAGELIT_DATABASE_ADMIN_URL:
    "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/ragelit_e2e",
  RAGELIT_DATABASE_URL:
    "postgresql+psycopg://ragelit_app:ragelit_app@127.0.0.1:5432/ragelit_e2e",
  RAGELIT_QDRANT_URL: "http://127.0.0.1:6333",
  RAGELIT_COOKIE_SECURE: "false",
}

const seeded = spawnSync("uv", ["run", "python", "-m", "tests.e2e_seed"], {
  cwd: backendDir,
  env: environment,
  stdio: "inherit",
})
if (seeded.status !== 0) process.exit(seeded.status ?? 1)

const api = spawn(
  "uv",
  [
    "run",
    "uvicorn",
    "app.main:create_app",
    "--factory",
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
  ],
  { cwd: backendDir, env: environment, stdio: "inherit" },
)

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => api.kill(signal))
}
api.on("exit", (code) => process.exit(code ?? 0))
