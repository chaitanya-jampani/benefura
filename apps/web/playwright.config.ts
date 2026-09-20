import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

import { API_ORIGIN, WEB_ORIGIN } from "./e2e/helpers/env";

const WEB_PORT = new URL(WEB_ORIGIN).port;
const API_PORT = new URL(API_ORIGIN).port;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  // Rasterizing 300 DPI pages is CPU-heavy; run specs one at a time for stable timings.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 180_000,
  expect: { timeout: 30_000 },
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: WEB_ORIGIN,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // pydantic-settings JSON-decodes list settings from the environment, so origins go in as JSON.
      command: `cd ../api && AI_MODE=fake CORS_ORIGINS='["${WEB_ORIGIN}"]' uv run uvicorn app.main:app --port ${API_PORT}`,
      cwd: path.resolve(__dirname),
      url: `${API_ORIGIN}/healthz`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      command: `pnpm build && pnpm exec serve out -l ${WEB_PORT}`,
      cwd: path.resolve(__dirname),
      url: WEB_ORIGIN,
      env: { NEXT_PUBLIC_API_BASE_URL: API_ORIGIN },
      reuseExistingServer: !process.env.CI,
      timeout: 600_000,
      stdout: "ignore",
      stderr: "pipe",
    },
  ],
});
