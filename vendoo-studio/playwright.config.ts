import { defineConfig, devices } from "@playwright/test";

// Browser tests run against the sandbox (scripts/sandbox.sh): fresh sample data,
// a fake AI provider, no network, and publishing refused. They cover the desktop
// layout and the phone layout. See e2e/README.md.
const API_PORT = process.env.VENDOO_STUDIO_PORT || "4329";
const WEB_PORT = process.env.VENDOO_STUDIO_WEB_PORT || "5184";

export default defineConfig({
  testDir: "e2e",
  testMatch: "**/*.e2e.ts",
  outputDir: "e2e/results",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never", outputFolder: "e2e/report" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "phone", use: { ...devices["iPhone 15"], browserName: "chromium" } },
  ],
  webServer: {
    command: "./scripts/sandbox.sh",
    env: { VENDOO_STUDIO_PORT: API_PORT, VENDOO_STUDIO_WEB_PORT: WEB_PORT },
    // Vite answers before the API has seeded, so wait for the script's own line.
    wait: { stdout: /Sandbox ready/ },
    stdout: "pipe",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
  },
});
