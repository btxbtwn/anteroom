// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { SubscriptionSummary } from "../../api/types";
import { desktopRuntime, WorkspaceRuntimeProvider, type WorkspaceRuntime } from "../../app/runtime";
import { PlanSettings } from "./PlanSettings";

const RUNTIME: WorkspaceRuntime = {
  ...desktopRuntime,
  hosted: true,
  account: { id: "11111111-1111-1111-1111-111111111111", email: "alice@sandbox.test" },
};

const PUBLIC_PLANS: SubscriptionSummary["plans"] = [
  {
    id: "free",
    name: "Free",
    tagline: "Try Anteroom with real listings",
    monthly_price_cents: 0,
    annual_price_cents: 0,
    monthly_credits: 50,
    monthly_listed_items: 10,
    recommended: false,
  },
  {
    id: "lister",
    name: "Lister",
    tagline: "Unlimited manual & bulk cross-listing",
    monthly_price_cents: 1499,
    annual_price_cents: 14990,
    monthly_credits: 100,
    monthly_listed_items: null,
    recommended: false,
  },
  {
    id: "pro",
    name: "Pro",
    tagline: "Full-time resellers with AI drafting & AI Studio",
    monthly_price_cents: 4999,
    annual_price_cents: 49990,
    monthly_credits: 1200,
    monthly_listed_items: null,
    recommended: true,
  },
];

function renderWithSummary(summary: SubscriptionSummary): string {
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  client.setQueryData(["subscription-plan", RUNTIME.account?.id], summary);
  try {
    return renderToStaticMarkup(
      <WorkspaceRuntimeProvider value={RUNTIME}>
        <QueryClientProvider client={client}>
          <PlanSettings />
        </QueryClientProvider>
      </WorkspaceRuntimeProvider>,
    );
  } finally {
    client.clear();
  }
}

it("shows Smart Credits, Free item cap, preview non-enforced note, credit rates, and available tiers", () => {
  const html = renderWithSummary({
    plan: {
      id: "free",
      name: "Free",
      tagline: "Try Anteroom with real listings",
      monthly_price_cents: 0,
      annual_price_cents: 0,
      monthly_credits: 50,
      monthly_listed_items: 10,
      billing_interval: "monthly",
      unlimited: false,
    },
    period: "2026-11",
    resets_at: "2026-12-01T00:00:00Z",
    limits_enforced: false,
    credits: {
      used: 12,
      limit: 50,
      remaining: 38,
      breakdown: {
        ai_listing: { count: 2, credits: 10 },
        photo_cutout: { count: 2, credits: 2 },
        photo_studio: { count: 0, credits: 0 },
        photo_refund: { count: 0, credits: 0 },
      },
    },
    listed_items: { used: 3, limit: 10, remaining: 7 },
    credit_weights: { ai_listing: 5, photo_cutout: 1, photo_studio: 5, manual_listing: 0 },
    plans: PUBLIC_PLANS,
  });

  expect(html).toContain("Plan &amp; Smart Credits");
  expect(html).toContain("Usage is tracked for preview.");
  expect(html).toContain("12 / 50");
  expect(html).toContain("3 / 10");
  expect(html).toContain("2 AI listings (10 cr)");
  expect(html).toContain("2 background cuts (2 cr)");
  expect(html).toContain("Dec 1, 2026");
  expect(html).toContain("$14.99/mo");
  expect(html).toContain("$49.99/mo");
  expect(html).toContain("Current");
  expect(html).toContain("Self-serve plan upgrades aren’t open yet; plan limits stay off until upgrades are available.");
});

it("shows truthful non-blocking copy past thresholds while enforcement is off", () => {
  const html = renderWithSummary({
    plan: {
      id: "free",
      name: "Free",
      tagline: "Try Anteroom with real listings",
      monthly_price_cents: 0,
      annual_price_cents: 0,
      monthly_credits: 50,
      monthly_listed_items: 10,
      billing_interval: "monthly",
      unlimited: false,
    },
    period: "2026-11",
    resets_at: "2026-12-01T00:00:00Z",
    limits_enforced: false,
    credits: {
      used: 65,
      limit: 50,
      remaining: 0,
      breakdown: {
        ai_listing: { count: 13, credits: 65 },
        photo_cutout: { count: 0, credits: 0 },
        photo_studio: { count: 0, credits: 0 },
        photo_refund: { count: 0, credits: 0 },
      },
    },
    listed_items: { used: 14, limit: 10, remaining: 0 },
    credit_weights: { ai_listing: 5, photo_cutout: 1, photo_studio: 5, manual_listing: 0 },
    plans: PUBLIC_PLANS,
  });

  expect(html).toContain("65 / 50");
  expect(html).toContain("14 / 10");
  expect(html).toContain("Caps aren’t enforced yet, so AI drafting and AI Studio continue working.");
  expect(html).toContain("Listing caps aren’t enforced yet, so manual, bulk, and cross-listing continue working.");
  expect(html).not.toContain("Monthly Smart Credits are used up.");
  expect(html).not.toContain("Upgrade to Lister ($14.99/mo)");
  expect(html).not.toContain("bg-destructive");
});

it("explains when Smart Credits and Free item limits are exhausted once enforcement is active", () => {
  const html = renderWithSummary({
    plan: {
      id: "free",
      name: "Free",
      tagline: "Try Anteroom with real listings",
      monthly_price_cents: 0,
      annual_price_cents: 0,
      monthly_credits: 50,
      monthly_listed_items: 10,
      billing_interval: "monthly",
      unlimited: false,
    },
    period: "2026-11",
    resets_at: "2026-12-01T00:00:00Z",
    limits_enforced: true,
    credits: {
      used: 50,
      limit: 50,
      remaining: 0,
      breakdown: {
        ai_listing: { count: 10, credits: 50 },
        photo_cutout: { count: 0, credits: 0 },
        photo_studio: { count: 0, credits: 0 },
        photo_refund: { count: 0, credits: 0 },
      },
    },
    listed_items: { used: 10, limit: 10, remaining: 0 },
    credit_weights: { ai_listing: 5, photo_cutout: 1, photo_studio: 5, manual_listing: 0 },
    plans: PUBLIC_PLANS,
  });

  expect(html).toContain("Monthly Smart Credits are used up.");
  expect(html).toContain("10-item Free limit");
  expect(html).not.toContain("Usage is tracked for preview.");
});

it("renders Unmetered status for internal Unlimited accounts without cap preview notice", () => {
  const html = renderWithSummary({
    plan: {
      id: "unlimited",
      name: "Unlimited",
      tagline: "Unmetered internal account",
      monthly_price_cents: 0,
      annual_price_cents: 0,
      monthly_credits: null,
      monthly_listed_items: null,
      billing_interval: "monthly",
      unlimited: true,
    },
    period: "2026-11",
    resets_at: "2026-12-01T00:00:00Z",
    limits_enforced: false,
    credits: {
      used: 240,
      limit: null,
      remaining: null,
      breakdown: {
        ai_listing: { count: 48, credits: 240 },
        photo_cutout: { count: 0, credits: 0 },
        photo_studio: { count: 0, credits: 0 },
        photo_refund: { count: 0, credits: 0 },
      },
    },
    listed_items: { used: 42, limit: null, remaining: null },
    credit_weights: { ai_listing: 5, photo_cutout: 1, photo_studio: 5, manual_listing: 0 },
    plans: PUBLIC_PLANS,
  });

  expect(html).toContain("Unmetered");
  expect(html).toContain("240 used · Unlimited");
  expect(html).toContain("42 · Unlimited");
  expect(html).not.toContain("Usage is tracked for preview.");
});
