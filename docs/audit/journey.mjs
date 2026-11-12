// The daily audit's hand-driven core journey, scripted (see docs/audit.md, Smoke step 6).
// New draft, three photos, generate, edit the title, reload, check the edit was kept,
// then check that publishing is refused and that another seller can't read the draft.
//
// Run against a sandbox only, after `cd vendoo-studio && npm ci`:
//   node docs/audit/journey.mjs local desktop <out-dir>     # ./sandbox.sh (UI 5174, API 4319)
//   node docs/audit/journey.mjs hosted phone <out-dir>      # ./sandbox.sh --hosted (UI 5175, API 4320)
// It prints one JSON line with the result, timings and any page errors, and
// saves a screenshot per step in <out-dir>. It lives outside e2e/ so the
// browser suites never pick it up.
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const require = createRequire(new URL("../../vendoo-studio/package.json", import.meta.url));
const { chromium, devices } = require("@playwright/test");

const [surface = "local", size = "desktop", out = "audit-shots"] = process.argv.slice(2);
const hosted = surface === "hosted";
const phone = size === "phone";
const ui = hosted ? "http://127.0.0.1:5175" : "http://127.0.0.1:5174";
const api = hosted ? "http://127.0.0.1:4320" : "http://127.0.0.1:4319";
mkdirSync(out, { recursive: true });

// Three solid-colour 1x1 PNGs: enough for an upload; the sandbox model never reads photos.
const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC";
const photos = ["A", "B", "C"].map(name => ({ name: `${name}.png`, mimeType: "image/png", buffer: Buffer.from(PNG, "base64") }));

const result = { surface: `${surface} ${size}`, timings: {}, errors: [] };
const browser = await chromium.launch();
const context = await browser.newContext(phone ? { ...devices["iPhone 13"] } : { viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("pageerror", error => result.errors.push(error.message));
const shot = name => page.screenshot({ path: `${out}/${surface}-${size}-${name}.png` });
const since = start => Math.round(performance.now() - start);

async function token(email) {
  const response = await fetch(`${api}/local/auth/v1/token?grant_type=password`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, password: "sandbox" }),
  });
  return (await response.json()).access_token;
}

try {
  const status = await (await fetch(`${api}/api/${hosted ? "health" : "status"}`)).json().catch(() => ({}));
  if (!hosted && status.sandbox !== true) throw new Error(`${api} is not a sandbox; refusing to run`);
  if (hosted) {
    await page.goto(`${ui}/?sign-in`);
    await page.getByLabel("Email address").fill("alice@sandbox.test");
    await page.getByLabel("Password").fill("sandbox");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
  } else {
    await page.goto(ui);
  }
  await page.waitForLoadState("networkidle").catch(() => {});
  // Exact name: a blank "New Listing" draft's row menu also matches "New listing".
  const newListing = page.getByRole("button", { name: "New listing", exact: true }).first();
  if (phone && !(await newListing.isVisible())) {
    await page.getByRole("button", { name: "Open navigation" }).click().catch(() => {});
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click().catch(() => {});
  }
  let start = performance.now();
  await newListing.click();
  await page.waitForURL(/[?&]listing=/, { timeout: 10_000 });
  result.timings.newDraftMs = since(start);
  result.id = new URL(page.url()).searchParams.get("listing");
  // On a phone a hosted draft opens on Listing; photos are staged in the chat composer.
  if (phone) await page.getByRole("tab", { name: "Chat" }).click().catch(() => {});
  await shot("1-new-draft");

  await page.locator('input[type="file"]').first().setInputFiles(photos);
  await page.waitForTimeout(1500);
  await shot("2-photos");

  start = performance.now();
  const generate = page.getByRole("button", { name: "Generate listing", exact: true });
  if (await generate.isVisible().catch(() => false)) await generate.click();
  else await page.getByRole("button", { name: /^Send/ }).first().click();
  // Edit only after generation has saved, or the stale-edit guard rightly refuses the edit.
  await page.getByText("Listing generated.").first().waitFor({ timeout: 150_000 });
  await page.waitForTimeout(3000);
  result.timings.generateMs = since(start);
  await shot("3-generated");

  if (phone) await page.getByRole("tab", { name: "Listing" }).click();
  const title = page.getByRole("group", { name: "General listing fields" }).getByRole("textbox", { name: "Title", exact: true });
  result.generatedTitle = await title.inputValue();
  const edited = `Audit edit ${Date.now()}`;
  start = performance.now();
  await title.fill(edited);
  result.timings.typeMs = since(start);
  await title.blur();
  await page.waitForTimeout(4000);
  result.staleEditBanner = await page.getByText(/Edits not saved/).count();
  await shot("4-edited");

  start = performance.now();
  await page.reload();
  if (phone) await page.getByRole("tab", { name: "Listing" }).click().catch(() => {});
  await title.waitFor();
  // The form is empty until the listing loads; poll before calling the edit lost.
  let after = "";
  for (let i = 0; i < 100 && after !== edited; i++) {
    after = await title.inputValue();
    if (after !== edited) await page.waitForTimeout(100);
  }
  result.timings.reopenMs = since(start);
  result.editKept = after === edited;
  await shot("5-reopened");

  // Publishing must be refused (403), with confirm and without.
  const alice = hosted ? { Authorization: `Bearer ${await token("alice@sandbox.test")}` } : {};
  result.publish = {};
  for (const path of ["native-marketplaces/ebay/list", "native-marketplaces/poshmark/update", "marketplace-listings/delist"]) {
    for (const body of [{ marketplaces: ["ebay"], confirm: true }, {}]) {
      const response = await fetch(`${api}/api/conversations/${result.id}/${path}`, {
        method: "POST", headers: { "content-type": "application/json", ...alice }, body: JSON.stringify(body),
      });
      result.publish[`${path}${body.confirm ? " +confirm" : ""}`] = response.status;
    }
  }
  result.publishRefused = Object.values(result.publish).every(code => code === 403);

  if (hosted) {
    // Seller isolation: Bob gets 404 on Alice's draft and photos.
    const bob = { Authorization: `Bearer ${await token("bob@sandbox.test")}` };
    result.bob = {};
    for (const path of [`drafts/${result.id}`, `drafts/${result.id}/photos`, `conversations/${result.id}/listing`]) {
      result.bob[path] = (await fetch(`${api}/api/${path}`, { headers: bob })).status;
    }
    result.isolated = Object.values(result.bob).every(code => code === 404);
  }
} catch (error) {
  result.failed = error.message.split("\n")[0];
  result.url = page.url();
  await shot("x-failure").catch(() => {});
  process.exitCode = 1;
} finally {
  console.log(JSON.stringify(result));
  await browser.close();
}
