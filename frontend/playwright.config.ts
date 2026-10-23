import { defineConfig } from "@playwright/test";

// The dashboard and the backend must already be running (CI starts them; see .github/workflows/ci.yml, or follow docs/MANUAL_TESTS_PHASE15.md).
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000", trace: "retain-on-failure", screenshot: "only-on-failure" },
});
