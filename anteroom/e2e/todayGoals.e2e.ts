import { expect, test } from "./fixtures";

test("Today refreshes after a saved goal deletion without a dashboard snapshot", async ({ page, phone }) => {
  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).first().click();
  await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Today", exact: true }).click();
  await page.getByRole("button", { name: "Add goal" }).click();
  await page.getByLabel("Goal target").fill("150");
  await page.getByLabel("Goal label").fill("Repair test goal");
  await page.getByRole("button", { name: "Save goal", exact: true }).click();
  const remove = page.getByRole("button", { name: "Delete Repair test goal" });
  await expect(remove).toBeVisible();

  await page.route("**/api/home/goals/*", async (route) => {
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    await route.fulfill({ response, json: { ok: true } });
  });
  await remove.click();
  await expect(remove).toHaveCount(0);
});
