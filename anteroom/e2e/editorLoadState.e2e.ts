import { createListing, expect, test, titleBox } from "./fixtures";

test("the editor shows a form-shaped placeholder while it loads and Retry when it fails", async ({page, request, phone}, info) => {
  test.setTimeout(90_000);
  const title = `Editor load state ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    // The editor's settings fail until the seller retries; hold the first answer so the placeholder shows.
    let failing = true;
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/settings/ui", async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      await held;
      if (failing) return route.fulfill({status: 500, json: {detail: "Settings could not be read."}});
      return route.continue();
    });
    await page.goto(`/?listing=${id}`);
    const skeleton = page.getByRole("status", {name: "Loading the listing"});
    await expect(skeleton).toBeVisible({timeout: 30_000});
    await page.screenshot({path: info.outputPath("editor-loading.png")});
    release();
    const failed = page.getByRole("alert").filter({hasText: "The editor didn't load"});
    await expect(failed).toBeVisible({timeout: 45_000});
    await page.screenshot({path: info.outputPath("editor-load-error.png")});
    failing = false;
    await failed.getByRole("button", {name: "Retry"}).click();
    await expect(failed).toBeHidden({timeout: 15_000});
    await expect(await titleBox(page, phone)).toBeVisible();
  } finally { await request.delete(`/api/conversations/${id}`); }
});
