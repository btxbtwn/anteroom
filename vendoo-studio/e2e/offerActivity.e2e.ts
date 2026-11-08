import type { Promotion, PromotionActivity, PromotionAutomation } from "../src/api/promotions";
import { expect, test } from "./fixtures";

const cases = [
  { marketplace: "poshmark", state: "done", confirmation: "request_accepted", recipient_count: null,
    label: "Request accepted", note: "Poshmark accepted the request. Buyer delivery is not verified." },
  { marketplace: "depop", state: "done", confirmation: "job_completed", recipient_count: null,
    label: "Offer batch completed", note: "Depop completed the offer batch. The buyer count is not available." },
  { marketplace: "mercari", state: "done", confirmation: "recipients_created", recipient_count: 3,
    label: "Offers created for 3 buyers", note: "Mercari confirmed offers were created for 3 buyers." },
  { marketplace: "poshmark", state: "unknown", confirmation: null, recipient_count: null,
    label: "not confirmed", note: "The result could not be confirmed. Check the marketplace before trying again." },
] as const;

for (const scenario of cases) {
  test(`offer activity shows ${scenario.confirmation ?? "unknown"} evidence without promising delivery`, async ({ page, request, phone }, info) => {
    expect((await (await request.get("/api/status")).json()).sandbox, "Use only the offline sandbox").toBe(true);
    const automation: PromotionAutomation = {
      id: "offer-evidence", name: "Offer activity review", marketplace: scenario.marketplace, action: "offer",
      scope: "all", conversation_ids: [], excluded_ids: [], min_days_listed: 0, times: [], timezone: "UTC",
      share_order: null, speed: null, daily_cap: null, discount_percent: 10, minimum_price: "20.00",
      min_likes: 1, shipping_label: null, enabled: false, available: true, paused_reason: null,
      next_run_at: null, last_run: null,
    };
    const send: Promotion = {
      id: "sample-offer", batch_id: "sample-batch", conversation_id: null, marketplace: scenario.marketplace,
      action: "offer", listing_id: "sample-wool-jacket", title: "Wool jacket", price: "40.00", offer_price: "36.00",
      floor: "20.00", discount_percent: 10, like_count: 7, shipping_options: [], shipping_discount_id: null,
      shipping_discount_label: null, state: scenario.state, confirmation: scenario.confirmation,
      recipient_count: scenario.recipient_count, note: scenario.note, marketplace_message: null,
      created_at: "2026-11-08T09:00:00Z", expires_at: null, finished_at: "2026-11-08T09:00:01Z",
    };
    const activity: PromotionActivity = { sends: [send], runs: [{
      id: "sample-run", started_at: send.created_at, finished_at: send.finished_at,
      sent: scenario.state === "done" ? 1 : 0, skipped: 0, failed: scenario.state === "unknown" ? 1 : 0, note: null,
    }] };
    const writes: string[] = [];
    // These are fixture reads, not marketplace requests. Reject any accidental offer action.
    await page.route(/\/api\/(?:promotion-automations|promotions)(?:\/|\?|$)/, async route => {
      if (route.request().method() !== "GET") {
        writes.push(route.request().url());
        await route.abort();
        return;
      }
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/activity")) await route.fulfill({ json: activity });
      else if (path.endsWith("/coverage")) await route.fulfill({ json: { items: [], included: 0 } });
      else if (path === "/api/promotion-automations") await route.fulfill({ json: {
        automations: [automation], supported: { [scenario.marketplace]: ["offer"] }, awaiting_capture: {},
        speeds: ["slow", "normal", "fast"], orders: ["newest", "oldest", "random"], max_daily_cap: 6000, shipping_labels: [],
      } });
      else await route.fallback();
    });
    await page.goto("/");
    if (phone) {
      await page.getByRole("button", { name: "Open navigation" }).click();
      await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Automations", exact: true }).click();
    } else await page.getByRole("button", { name: "Automations", exact: true }).click();
    const openActivity = async () => {
      await page.getByRole("button", { name: "More for Offer activity review" }).click();
      await page.getByRole("menuitem", { name: "Listings and activity", exact: true }).click();
    };
    await openActivity();
    const dialog = page.getByRole("dialog", { name: "Offer activity review", exact: true });
    await expect(dialog.getByText(scenario.label, { exact: false })).toBeVisible();
    await expect(dialog.getByText(scenario.note, { exact: true })).toBeVisible();
    await expect(dialog.getByText("Counts are listing actions, not buyers.", { exact: false })).toBeVisible();
    await expect(dialog.getByText(/\d listing actions completed/)).toBeVisible();
    await expect(dialog.getByText(/\d sent/)).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: "Check result", exact: true })).toHaveCount(scenario.state === "unknown" ? 1 : 0);
    for (const theme of ["light", "dark"]) {
      await page.evaluate(theme => {
        document.documentElement.classList.toggle("dark", theme === "dark");
        document.documentElement.style.colorScheme = theme;
      }, theme);
      await dialog.getByText(scenario.note, { exact: true }).scrollIntoViewIfNeeded();
      const name = `offer-${scenario.confirmation ?? "unknown"}-${info.project.name}-${theme}`;
      const path = info.outputPath(`${name}.png`);
      await page.screenshot({ path, animations: "disabled" });
      await info.attach(name, { path, contentType: "image/png" });
    }
    // Dismiss and reopen without sending, settling or enabling anything.
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await openActivity();
    await expect(dialog.getByText(scenario.note, { exact: true })).toBeVisible();
    expect(writes, "Viewing offer evidence must not change marketplace state").toEqual([]);
  });
}
