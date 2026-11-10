import { test, expect, createListing, openListing, openForm, titleBox } from "./fixtures";

test("draft editing stays available without repeated Chrome instructions", async ({ page, request, phone }, testInfo) => {
  const title = `Chrome requirements ${testInfo.project.name}`;
  const id = await createListing(request, title);
  try {
    await openListing(page, phone, title);
    await expect(await titleBox(page, phone)).toBeEditable();
    await openForm(page, phone, "Mercari");
    await expect(page.getByText("Requires desktop Chrome with the Bridge extension connected", { exact: false })).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("chrome-requirements.png"), fullPage: true });
    await page.evaluate(() => document.documentElement.classList.remove("dark"));
    await page.screenshot({ path: testInfo.outputPath("chrome-requirements-light.png"), fullPage: true });
    await openForm(page, phone, "eBay");
    await expect(page.getByText("Requires desktop Chrome with the Bridge extension connected", { exact: false })).toHaveCount(0);
  } finally {
    await page.goto("about:blank");
    await request.delete(`/api/conversations/${id}`);
  }
});
