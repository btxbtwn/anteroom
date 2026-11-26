import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "./fixtures";

// Only seed this test's terminal receipts in the disposable sandbox already
// started by Playwright. Never send offers or open a marketplace connection.
const apiPort = process.env.ANTEROOM_PORT || "4329";
const sandboxDir = process.env.ANTEROOM_SANDBOX_DIR || join(tmpdir(), `anteroom-sandbox-${apiPort}`);
const seed = String.raw`
from datetime import UTC, datetime, timedelta
from anteroom.database import SessionLocal, load_models
from anteroom.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
from anteroom.models.listing_promotion import ListingPromotion
load_models()
now = datetime.now(UTC).replace(tzinfo=None)
with SessionLocal() as db:
    db.query(EbayOfferAttempt).filter(EbayOfferAttempt.rule_id == 'offer-analytics-e2e').delete()
    db.query(EbayOfferRule).filter(EbayOfferRule.id == 'offer-analytics-e2e').delete()
    db.query(ListingPromotion).filter(ListingPromotion.batch_id == 'offer-analytics-e2e').delete()
    db.add(EbayOfferRule(id='offer-analytics-e2e', account_id='sandbox-analytics', listing_id='sandbox-analytics', discount_percent=10, minimum_price_cents=100))
    db.flush()
    for count in (2, 0):
        db.add(EbayOfferAttempt(rule_id='offer-analytics-e2e', current_price_cents=1000, offer_price_cents=900, discount_percent=10, minimum_price_cents=100, state='sent', recipient_count=count, created_at=now, sent_at=now, expires_at=now+timedelta(days=1)))
    for market, evidence, count in [('mercari','recipients_created',3), ('mercari','recipients_created',None), ('poshmark','request_accepted',None), ('depop','job_completed',None)]:
        db.add(ListingPromotion(batch_id='offer-analytics-e2e', marketplace=market, action='offer', listing_id='sandbox-analytics', state='done', confirmation=evidence, recipient_count=count, created_at=now, sent_at=now, expires_at=now+timedelta(days=1)))
    db.commit()
`;

let acceptedRequests = 0;
let poshmarkSent = 0;
let originalTheme: string | undefined;
let sandboxVerified = false;
let fixturesSeeded = false;

test.beforeAll(async ({ request }) => {
  expect(apiPort).not.toBe("4318");
  const status = await request.get("/api/status");
  expect((await status.json()).sandbox).toBe(true);
  sandboxVerified = true;
  originalTheme = (await (await request.get("/api/settings/ui")).json()).theme;
  const today = new Date().toISOString().slice(0, 10);
  const baseline = await (await request.get(`/api/analytics/offers?start=${today}&end=${today}&timezone=UTC`)).json();
  const poshmark = baseline.marketplaces.find((row: { marketplace: string }) => row.marketplace === "poshmark");
  acceptedRequests = poshmark.accepted_requests + 1;
  // Offers sent counts every accepted send, including the sandbox's sample sends without a receipt.
  poshmarkSent = poshmark.confirmed_sends + poshmark.accepted_requests + poshmark.completed_jobs + poshmark.unverified + 1;
  execFileSync(".venv/bin/python", ["-c", seed], { env: { ...process.env,
    PYTHONPATH: "server", ANTEROOM_DATA_DIR: sandboxDir, ANTEROOM_DATABASE_URL: "", ANTEROOM_HOSTED: "0", ANTEROOM_SANDBOX: "1" } });
  fixturesSeeded = true;
});

test.afterAll(async ({ request }) => {
  // Playwright calls teardown even when a setup safety assertion failed.
  if (!sandboxVerified || !fixturesSeeded) return;
  if (originalTheme !== undefined) {
    expect((await request.put("/api/settings/ui", { data: { theme: originalTheme } })).ok()).toBe(true);
  }
  const cleanup = String.raw`
from anteroom.database import SessionLocal, load_models
from anteroom.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
from anteroom.models.listing_promotion import ListingPromotion
load_models()
with SessionLocal() as db:
    db.query(EbayOfferAttempt).filter(EbayOfferAttempt.rule_id == 'offer-analytics-e2e').delete()
    db.query(EbayOfferRule).filter(EbayOfferRule.id == 'offer-analytics-e2e').delete()
    db.query(ListingPromotion).filter(ListingPromotion.batch_id == 'offer-analytics-e2e').delete()
    db.commit()
`;
  execFileSync(".venv/bin/python", ["-c", cleanup], { env: { ...process.env,
    PYTHONPATH: "server", ANTEROOM_DATA_DIR: sandboxDir, ANTEROOM_DATABASE_URL: "", ANTEROOM_HOSTED: "0", ANTEROOM_SANDBOX: "1" } });
});

for (const theme of ["light", "dark"]) {
  test(`offer activity is usable in ${theme}`, async ({ page, request, phone }, testInfo) => {
    expect((await request.put("/api/settings/ui", { data: { theme } })).ok()).toBe(true);
    await page.addInitScript((value) => localStorage.setItem("anteroom:theme", value), theme);
    await page.goto("/");
    if (phone) {
      await page.getByRole("button", { name: "Open navigation" }).click();
      await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Automations", exact: true }).click();
    } else await page.getByRole("button", { name: "Automations", exact: true }).click();
    const section = page.getByRole("region", { name: "Offer activity" });
    const automations = page.getByRole("region", { name: "Your automations" });
    await expect(automations).toBeVisible();
    expect(await section.evaluate((node) => Boolean(node.compareDocumentPosition(
      document.querySelector('[aria-label="Your automations"]')!,
    ) & Node.DOCUMENT_POSITION_PRECEDING))).toBe(true);
    await expect(section.getByText(/Weeks start Monday/)).toHaveCount(0);
    await expect(section.getByRole("combobox")).toHaveText("Today");
    const group = (name: string) => section.getByRole("group", { name: `${name} offer activity`, exact: true });
    await expect(group("eBay").getByText("2 offers sent", { exact: true })).toBeVisible();
    await expect(group("eBay").getByText("Reached 2 buyers, as eBay reported", { exact: true })).toBeVisible();
    await expect(group("Mercari").getByText("Reached 3 buyers, as Mercari reported", { exact: true })).toBeVisible();
    for (const marketplace of ["eBay", "Mercari"]) {
      await expect(group(marketplace).getByText("1 offer with no marketplace receipt saved", { exact: true })).toBeVisible();
    }
    await expect(group("Poshmark").getByText(`${poshmarkSent} ${poshmarkSent === 1 ? "offer" : "offers"} sent`, { exact: true })).toBeVisible();
    await expect(group("Poshmark").getByText(`Poshmark accepted ${acceptedRequests === 1 ? "it" : `all ${acceptedRequests}`} but doesn't say how many likers got each offer`, { exact: true })).toBeVisible();
    await expect(group("Depop").getByText("Depop finished it but doesn't say how many likers got each offer", { exact: true })).toBeVisible();
    await expect(section.getByText("Not available")).toHaveCount(0);
    await expect(section.getByText(/Marketplaces don’t report which offers led to sales/)).toBeVisible();
    await expect(page.locator("html")).toHaveCSS("color-scheme", theme);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`offer-analytics-${testInfo.project.name}-${theme}.png`), fullPage: true });
    await section.getByRole("button", { name: "Offer log" }).click();
    const log = page.getByRole("dialog", { name: "Offer log" });
    const sends = log.getByRole("list", { name: "Offers sent" });
    await expect(sends.getByText("Sent to 3 buyers", { exact: true })).toBeVisible();
    await expect(sends.getByText("Sent to 2 buyers", { exact: true })).toBeVisible();
    await expect(sends.getByText("Sent · Depop doesn't report how many", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`offer-log-${testInfo.project.name}-${theme}.png` ) });
    await page.keyboard.press("Escape");
    await expect(log).toBeHidden();
    await section.getByRole("combobox").click();
    await page.getByRole("option", { name: "This week", exact: true }).click();
    await expect(section.getByText("Reached 2 buyers, as eBay reported", { exact: true })).toBeVisible();
    await section.getByRole("combobox").click();
    await page.getByRole("option", { name: "This month", exact: true }).click();
    await expect(section.getByText("Reached 3 buyers, as Mercari reported", { exact: true })).toBeVisible();
    await section.getByRole("combobox").click();
    await page.getByRole("option", { name: "Custom dates", exact: true }).click();
    await expect(section.getByLabel("From", { exact: true })).toBeVisible();
    await section.getByLabel("From", { exact: true }).fill("2025-01-01");
    await section.getByLabel("Through", { exact: true }).fill("2025-01-01");
    const formattedDay = await page.evaluate(() => new Date(2025, 0, 1).toLocaleDateString(undefined, {
      month: "short", day: "numeric", year: "numeric",
    }));
    await expect(section.getByText(formattedDay, { exact: true })).toBeVisible();
    await expect(section.getByText("0 offers sent", { exact: true })).toHaveCount(4);
    await section.getByLabel("From", { exact: true }).fill("2025-01-02");
    await expect(section.getByText("Choose a start on or before the end.")).toBeVisible();
    await section.getByLabel("From", { exact: true }).fill("");
    await expect(section.getByRole("combobox")).toHaveText("Custom dates");
  });
}
