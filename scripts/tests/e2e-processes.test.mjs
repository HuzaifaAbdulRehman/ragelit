import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import { once } from "node:events"
import { cpSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"
import test from "node:test"

test("Playwright process-group shutdown closes fixture pipes", {
  skip: process.platform === "win32",
  timeout: 10_000,
}, async () => {
  const root = mkdtempSync(path.join(tmpdir(), "ragelit-process-test-"))
  const children = []
  let launcher
  try {
    const frontend = path.join(root, "frontend/tests")
    const bin = path.join(root, "bin")
    const pythonDir = path.join(root, "backend/.venv/bin")
    for (const dir of [frontend, bin, pythonDir])
      mkdirSync(dir, { recursive: true })
    cpSync(
      fileURLToPath(
        new URL("../../frontend/tests/start-api.mjs", import.meta.url),
      ),
      path.join(frontend, "start-api.mjs"),
    )
    writeFileSync(path.join(bin, "uv"), "#!/bin/sh\nexit 0\n", { mode: 0o755 })
    writeFileSync(
      path.join(pythonDir, "python"),
      `#!/usr/bin/env node\nconsole.log('READY ' + process.pid)\nsetInterval(() => {}, 1000)\n`,
      { mode: 0o755 },
    )
    launcher = spawn(process.execPath, [path.join(frontend, "start-api.mjs")], {
      detached: true,
      env: {
        ...process.env,
        TMPDIR: root,
        PATH: `${bin}${path.delimiter}${process.env.PATH}`,
      },
      stdio: ["ignore", "pipe", "pipe"],
    })
    let output = ""
    launcher.stdout.on("data", (chunk) => {
      output += chunk
      children.splice(
        0,
        children.length,
        ...Array.from(output.matchAll(/READY (\d+)/g), (match) =>
          Number(match[1]),
        ),
      )
    })
    await new Promise((resolve, reject) => {
      const deadline = setTimeout(
        () => reject(new Error("Fixture did not start")),
        3000,
      )
      const poll = setInterval(() => {
        if (children.length !== 2) return
        clearInterval(poll)
        clearTimeout(deadline)
        resolve()
      }, 10)
      deadline.unref()
      poll.unref()
    })
    const closed = once(launcher, "close")
    process.kill(-launcher.pid, "SIGKILL")
    const result = await Promise.race([
      closed.then(() => "closed"),
      new Promise((resolve) => setTimeout(() => resolve("open pipes"), 1000)),
    ])
    assert.equal(
      result,
      "closed",
      "Detached API/worker children kept Playwright's pipes open",
    )
  } finally {
    for (const pid of [...children, launcher?.pid].filter(Boolean)) {
      try {
        process.kill(pid, "SIGKILL")
      } catch (error) {
        if (error.code !== "ESRCH") throw error
      }
    }
    rmSync(root, { recursive: true, force: true })
  }
})
