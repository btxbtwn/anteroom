import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "./fixtures";

// Only seed this test's terminal receipts in the disposable sandbox already
// started by Playwright. Never send offers or open a marketplace connection.
const apiPort = process.env.VENDOO_STUDIO_PORT || "4329";
const sandboxDir = process.env.ANTEROOM_SANDBOX_DIR || join(tmpdir(), `anteroom-sandbox-${apiPort}`);
const seed = String.raw`
from datetime import UTC, datetime, timedelta
from vendoo_studio.database import SessionLocal, load_models
from vendoo_studio.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
from vendoo_studio.models.listing_promotion import ListingPromotion
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
  acceptedRequests = baseline.marketplaces.find((row: { marketplace: string }) => row.marketplace === "poshmark").accepted_requests + 1;
  execFileSync(".venv/bin/python", ["-c", seed], { env: { ...process.env,
    PYTHONPATH: "server", VENDOO_STUDIO_DATA_DIR: sandboxDir, ANTEROOM_DATABASE_URL: "", ANTEROOM_HOSTED: "0", ANTEROOM_SANDBOX: "1" } });
  fixturesSeeded = true;
});

test.afterAll(async ({ request }) => {
  // Playwright calls teardown even when a setup safety assertion failed.
  if (!sandboxVerified || !fixturesSeeded) return;
  if (originalTheme !== undefined) {
    expect((await request.put("/api/settings/ui", { data: { theme: originalTheme } })).ok()).toBe(true);
  }
  const cleanup = String.raw`
from vendoo_studio.database import SessionLocal, load_models
from vendoo_studio.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
from vendoo_studio.models.listing_promotion import ListingPromotion
load_models()
with SessionLocal() as db:
    db.query(EbayOfferAttempt).filter(EbayOfferAttempt.rule_id == 'offer-analytics-e2e').delete()
    db.query(EbayOfferRule).filter(EbayOfferRule.id == 'offer-analytics-e2e').delete()
    db.query(ListingPromotion).filter(ListingPromotion.batch_id == 'offer-analytics-e2e').delete()
    db.commit()
`;
  execFileSync(".venv/bin/python", ["-c", cleanup], { env: { ...process.env,
    PYTHONPATH: "server", VENDOO_STUDIO_DATA_DIR: sandboxDir, ANTEROOM_DATABASE_URL: "", ANTEROOM_HOSTED: "0", ANTEROOM_SANDBOX: "1" } });
});

for (const theme of ["light", "dark"]) {
  test(`offer activity is usable in ${theme}`, async ({ page, request, phone }, testInfo) => {
    expect((await request.put("/api/settings/ui", { data: { theme } })).ok()).toBe(true);
    await page.addInitScript((value) => localStorage.setItem("vendoo-studio:theme", value), theme);
    await page.goto("/");
    if (phone) {
      await page.getByRole("button", { name: "Open navigation" }).click();
      await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Automations", exact: true }).click();
    } else await page.getByRole("button", { name: "Automations", exact: true }).click();
    const section = page.getByRole("region", { name: "Offer activity" });
    await expect(section.getByRole("combobox")).toHaveText("Today");
    await expect(section.getByText("2 confirmed recipient offers", { exact: true })).toBeVisible();
    await expect(section.getByText("3 confirmed recipient offers", { exact: true })).toBeVisible();
    await expect(section.getByText(`${acceptedRequests} accepted requests · recipients not reported`)).toBeVisible();
    await expect(section.getByText("1 completed bulk jobs · recipients not reported")).toBeVisible();
    for (const marketplace of ["eBay", "Mercari"]) {
      await expect(section.getByRole("group", { name: `${marketplace} offer activity`, exact: true })
        .getByText("0 unknown · 1 unverified · 0 failed · 0 pending", { exact: true })).toBeVisible();
    }
    await expect(section.getByText(/Converted sales: Not available/)).toBeVisible();
    await expect(page.locator("html")).toHaveCSS("color-scheme", theme);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`offer-analytics-${testInfo.project.name}-${theme}.png`), fullPage: true });
    await section.getByRole("combobox").click();
    await page.getByRole("option", { name: "This week", exact: true }).click();
    await expect(section.getByText("2 confirmed recipient offers", { exact: true })).toBeVisible();
    await section.getByRole("combobox").click();
    await page.getByRole("option", { name: "This month", exact: true }).click();
    await expect(section.getByText("3 confirmed recipient offers", { exact: true })).toBeVisible();
    await section.getByRole("combobox").click();
    await page.getByRole("option", { name: "Custom dates", exact: true }).click();
    await expect(section.getByLabel("From", { exact: true })).toBeVisible();
    await section.getByLabel("From", { exact: true }).fill("2025-01-01");
    await section.getByLabel("Through", { exact: true }).fill("2025-01-01");
    await expect(section.getByText("Not available", { exact: true })).toHaveCount(4);
    await section.getByLabel("From", { exact: true }).fill("2025-01-02");
    await expect(section.getByText("Choose a start on or before the end.")).toBeVisible();
  });
}
