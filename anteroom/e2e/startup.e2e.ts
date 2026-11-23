import { expect, test } from "./fixtures";

test("a failed startup download offers to reload the workspace", async ({ page }) => {
  const entryModule = "**/src/main.tsx";
  await page.route(entryModule, route => route.fulfill({ status: 404 }));
  await page.goto("/");

  const startup = page.getByRole("main", { name: "Opening Anteroom" });
  await expect(startup.getByRole("alert")).toHaveText("Could not load Anteroom. Reload to try again.");
  await expect(startup.locator(".startup-spinner")).toBeHidden();
  await page.unroute(entryModule);
  await startup.getByRole("button", { name: "Reload Anteroom" }).click();
  await expect(startup).toHaveCount(0);
});
