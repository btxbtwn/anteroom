import { test, expect } from "@playwright/test";

test.setTimeout(60_000);

for (const theme of ["light", "dark"]) {
  test(`startup is visible before JavaScript loads in ${theme} mode`, async ({ page }, info) => {
    if (info.project.name === "desktop") await page.setViewportSize({ width: 1440, height: 900 });
    await page.addInitScript(value => localStorage.setItem("vendoo-studio:theme", value), theme);
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/src/cloud/main.tsx", async route => { await gate; await route.continue(); });
    try {
      await page.goto("/?sign-in", { waitUntil: "commit" });
      await expect(page.getByRole("heading", { name: "Anteroom", exact: true })).toBeVisible();
      await expect(page.getByRole("status")).toHaveText("Getting things ready…");
      await expect(page.locator("html")).toHaveClass(theme === "dark" ? "dark" : "");
      const screen = page.getByRole("main");
      await expect(screen).toHaveCSS("height", `${page.viewportSize()!.height}px`);
      await page.screenshot({ path: info.outputPath(`startup-${theme}.png`) });
    } finally { release(); }
    await expect(page.getByLabel("Email address")).toBeVisible({ timeout: 30_000 });
  });
}

test("workspace startup supports a slow response, reload and an actionable error", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/?sign-in");
  let fail = false;
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/runtime", async route => {
    if (!fail) { await gate; await route.continue(); }
    else await route.fulfill({ status: 503, json: { detail: "Unavailable" } });
  });
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  try {
    await expect(page.getByRole("status")).toHaveText("Getting things ready…");
    await expect(page.getByRole("button", { name: "Reload Anteroom" })).toHaveCount(0);
    await expect(page.locator(".startup-spinner")).toHaveCSS("animation-name", "none");
    await expect(page.getByRole("status")).toContainText("Taking longer than usual", { timeout: 15_000 });
    fail = true;
    await page.getByRole("button", { name: "Reload Anteroom" }).click();
    await expect(page.getByRole("alert")).toContainText("Could not open your workspace");
    await expect(page.locator(".startup-spinner")).toHaveCount(0);
    fail = false;
    release();
    await page.getByRole("button", { name: "Reload Anteroom" }).click();
    await expect(page.getByRole("heading", { name: "Anteroom", exact: true })).toHaveCount(0, { timeout: 20_000 });
    await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
  } finally { release(); }
});
