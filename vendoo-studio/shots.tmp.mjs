import { chromium, devices } from "@playwright/test";
const [out, tag] = [process.argv[2], process.argv[3]];
const base = "http://127.0.0.1:5203";
const browser = await chromium.launch({ args: ["--no-proxy-server"] });
const title = "Madewell 28 Black High Rise Skinny Jeans";
async function desktop() {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
  const page = await ctx.newPage(); page.setDefaultTimeout(600000);
  await page.goto(base, { timeout: 600000 }); 
  await page.getByRole("list", { name: "Listings" }).getByRole("link", { name: title, exact: true }).click();
  await page.waitForTimeout(6000);
  await page.screenshot({ path: `${out}/${tag}-desktop-listing.png` });
  await page.getByRole("button", { name: /List everywhere/ }).first().click();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${out}/${tag}-desktop-list-everywhere.png` });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Select listings", exact: true }).click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${out}/${tag}-desktop-select.png` });
  await ctx.close();
}
async function selectOnly() {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
  const page = await ctx.newPage(); page.setDefaultTimeout(600000);
  await page.goto(base, { timeout: 600000 });
  await page.getByRole("button", { name: "Select listings", exact: true }).click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${out}/${tag}-desktop-select.png` });
  await page.getByRole("list", { name: "Listings" }).getByRole("checkbox").nth(1).click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${out}/${tag}-desktop-select-one.png` });
  const more = page.getByRole("button", { name: "More", exact: true });
  if (await more.count()) { await more.click(); await page.waitForTimeout(800); await page.screenshot({ path: `${out}/${tag}-desktop-select-more.png` }); }
  await ctx.close();
}
async function phone() {
  const ctx = await browser.newContext({ ...devices["iPhone 15"], colorScheme: "dark" });
  const page = await ctx.newPage(); page.setDefaultTimeout(600000);
  await page.goto(base, { timeout: 600000 });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  await page.getByRole("button", { name: "Select listings", exact: true }).click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${out}/${tag}-phone-select.png` });
  await page.getByRole("button", { name: /Cancel/ }).last().click();
  await page.getByRole("list", { name: "Listings" }).getByRole("link", { name: title, exact: true }).click();
  await page.waitForTimeout(6000);
  await page.screenshot({ path: `${out}/${tag}-phone-listing.png` });
  await ctx.close();
}
const only = process.argv[4]; for (const step of [desktop, selectOnly, phone].filter(f => only ? only.split(',').includes(f.name) : f !== selectOnly)) { try { await step(); } catch (e) { console.error(step.name, e.message.split("\n")[0]); } }
await browser.close();
