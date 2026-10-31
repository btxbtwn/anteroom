import { defineConfig, devices } from "@playwright/test";

const apiPort = process.env.VENDOO_STUDIO_PORT || "4341";
const webPort = process.env.VENDOO_STUDIO_WEB_PORT || "5195";
export default defineConfig({
  testDir: "hosted-e2e",
  testMatch: "**/*.e2e.ts",
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  outputDir: "e2e/results/hosted",
  use: { baseURL: `http://127.0.0.1:${webPort}`, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["iPhone 15"], browserName: "chromium" } },
  ],
  webServer: {
    command: "./scripts/hosted-sandbox.sh",
    env: { VENDOO_STUDIO_PORT: apiPort, VENDOO_STUDIO_WEB_PORT: webPort },
    url: `http://127.0.0.1:${webPort}/cloud.html`,
    timeout: 120_000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
  },
});
