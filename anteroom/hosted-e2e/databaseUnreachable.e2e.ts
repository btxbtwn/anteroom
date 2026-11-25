import { test, expect, type Page } from "@playwright/test";

test.setTimeout(90_000);

/** Postgres refuses connections: every API answer is the database 503, and /api/ready fails too. */
async function takeDatabaseDown(page: Page) {
  let down = true;
  await page.route(url => url.pathname.startsWith("/api/"), async route => {
    if (!down) return route.continue();
    const ready = new URL(route.request().url()).pathname === "/api/ready";
    await route.fulfill(ready
      ? { status: 503, contentType: "application/json", body: '{"status":"unavailable"}' }
      : { status: 503, contentType: "application/json", body: '{"detail":"Cloud data is temporarily unavailable"}',
          headers: { "Retry-After": "5", "X-Anteroom-Reason": "database_unavailable" } });
  });
  return () => { down = false; };
}

test("an unreachable database says so instead of loading forever, and clears once /api/ready passes", async ({ page }, info) => {
  if (info.project.name === "desktop") await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible({ timeout: 30_000 });

  const bringBack = await takeDatabaseDown(page);
  // Reopening shows the workspace saved on this device while the server is asked again.
  await page.reload();
  const banner = page.getByText("Can't reach your data right now — retrying");
  await expect(banner).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: info.outputPath(`database-unreachable-${info.project.name}.png`) });

  bringBack();
  // The 5-second /api/ready check passes: the banner goes away by itself.
  await expect(banner).toBeHidden({ timeout: 15_000 });
});
