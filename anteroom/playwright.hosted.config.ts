import { defineConfig, devices } from "@playwright/test";

const apiPort = process.env.ANTEROOM_PORT || "4341";
const webPort = process.env.ANTEROOM_WEB_PORT || "5195";
export default defineConfig({
  testDir: "hosted-e2e",
  testMatch: "**/*.e2e.ts",
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [
    ["list"],
    ...(process.env.GITHUB_ACTIONS === "true" ? [["github"] as [string]] : []),
    ["html", { open: "never", outputFolder: "e2e/report" }],
  ] : "list",
  outputDir: "e2e/results/hosted",
  use: { baseURL: `http://127.0.0.1:${webPort}`, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["iPhone 15"], browserName: "chromium" } },
  ],
  webServer: {
    command: "./scripts/hosted-sandbox.sh",
    env: { ANTEROOM_PORT: apiPort, ANTEROOM_WEB_PORT: webPort },
    url: `http://127.0.0.1:${webPort}/cloud.html`,
    timeout: 120_000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
  },
});
