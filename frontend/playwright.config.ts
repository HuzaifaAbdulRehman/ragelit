import { defineConfig } from "@playwright/test"

delete process.env.NO_COLOR

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "node tests/start-api.mjs",
      port: 8000,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "npm run dev -- --host 127.0.0.1",
      port: 5173,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
