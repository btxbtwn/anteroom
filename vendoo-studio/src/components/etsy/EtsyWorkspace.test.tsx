import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EtsyOrders as EtsyOrderPage, EtsyShop } from "../../api/etsy";
import { EtsyWorkspace } from "./EtsyWorkspace";
import { EtsyDashboard, EtsyListings, EtsyOrders, EtsySaleAlerts, etsyMoney } from "./SellerPanels";
import { api } from "../../api/client";

const { useQuery, useMutation, account } = vi.hoisted(() => ({
  useQuery: vi.fn(), useMutation: vi.fn(),
  account: { connected: true, label: "VintageFinds", connectedAt: 123 },
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery, useMutation, keepPreviousData: vi.fn(),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

const shop: EtsyShop = {
  id: "42", name: "VintageFinds", title: "Clothing from another era", url: "https://www.etsy.com/shop/VintageFinds",
  activeListings: 42, soldItems: 350, favorites: 90, reviewCount: 30, rating: 4.9, onVacation: false,
};
const orders: EtsyOrderPage = {
  rows: [{ id: "500", status: "paid", paid: true, shipped: false, needsShipping: true,
    createdAt: "2026-10-01T12:00:00Z", total: { amount: "35", currency: "USD" },
    items: [{ id: "1", title: "Vintage wool coat", quantity: 1, sku: "COAT-1" }],
  }],
  total: 48, nextOffset: 25,
  summary: { paidOrders: 1, needsShipping: 1, paidTotals: [{ amount: "35", currency: "USD" }], missingTotals: 0 },
};
const period = { start: "2026-10-01", end: "2026-10-24", valid: true };

beforeEach(() => {
  account.connected = true;
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useQuery.mockImplementation(({ queryKey }: { queryKey: readonly unknown[] }) => {
    if (queryKey[0] === "direct-connections") return { data: { accounts: { etsy: account } } };
    if (queryKey[0] === "etsy-not-allowed") return { data: { items: [] }, isSuccess: true };
    const section = queryKey[1];
    const data = section === "shop" ? shop : section === "orders" ? orders : {
      rows: [{ id: "100", title: "Vintage jacket", state: "active", url: "https://www.etsy.com/listing/100",
        price: { amount: "50", currency: "EUR" }, quantity: 1, favorites: 4, createdAt: "2026-10-01T12:00:00Z" }],
      total: 48, nextOffset: 25,
    };
    return { data, error: null, isPending: false, isFetching: false };
  });
});

describe("EtsyWorkspace", () => {
  it("shows the connected shop dashboard, seller tabs, and live metrics", () => {
    const html = renderToStaticMarkup(<EtsyWorkspace onOpenListing={vi.fn()} />);
    expect(html).toContain('aria-label="Etsy workspace"');
    for (const title of ["Dashboard", "Orders", "Listings"]) expect(html).toContain(`${title}</button>`);
    expect(html).not.toContain("Marketing</button>");
    expect(html).toContain("VintageFinds");
    expect(html).toContain("Active listings");
    expect(html).toContain("$35.00");
    expect(html).toContain("Order #500");
    expect(html).toContain("4.9 / 5");
    expect(html).not.toContain("Order earnings");
    expect(html).not.toContain(">Publish<");
  });

  it("shows a connection prompt instead of invented zero metrics when signed out", () => {
    account.connected = false;
    const html = renderToStaticMarkup(<EtsyWorkspace onOpenListing={vi.fn()} />);
    expect(html).toContain("Connect your Etsy shop");
    expect(html).not.toContain("Paid order totals");
    expect(html).not.toContain("$0.00");
    const shopCall = useQuery.mock.calls.map(([options]) => options).find((options) => options.queryKey[1] === "shop");
    expect(shopCall.enabled).toBe(false);
  });
});

describe("EtsyDashboard", () => {
  it("labels partial page totals and never combines currencies", () => {
    useQuery.mockReturnValue({ data: { ...orders, summary: { ...orders.summary,
      paidTotals: [{ amount: "35", currency: "USD" }, { amount: "80", currency: "JPY" }], missingTotals: 1,
    } } });
    const html = renderToStaticMarkup(<EtsyDashboard shop={shop} shopError={null} scope="shop-1" period={period} onOpenOrders={vi.fn()} />);
    expect(html).toContain("$35.00 · ¥80");
    expect(html).not.toContain("$115");
    expect(html).toContain("newest 1 of 48 orders");
    expect(html).toContain("before refunds and fees");
    expect(html).toContain("Totals are unavailable for 1 paid order");
  });

  it("keeps missing data unavailable and displays errors", () => {
    useQuery.mockReturnValue({ error: new Error("Etsy denied access"), isPending: false });
    const html = renderToStaticMarkup(<EtsyDashboard shopError={new Error("Reconnect Etsy")} scope="shop-1" period={period} onOpenOrders={vi.fn()} />);
    expect(html).toContain("Not available");
    expect(html).toContain("Etsy denied access");
    expect(html).toContain("Reconnect Etsy");
    expect(html).not.toContain("$0.00");
  });

  it("disables reads and hides old totals for invalid dates", () => {
    const html = renderToStaticMarkup(<EtsyDashboard shop={shop} shopError={null} scope="shop-1" period={{ ...period, valid: false }} onOpenOrders={vi.fn()} />);
    const options = useQuery.mock.lastCall?.[0];
    expect(options.enabled).toBe(false);
    expect(html).not.toContain("$35.00");
    expect(html).not.toContain("Order #500");
  });
});

describe("Etsy seller pages", () => {
  it("keeps Etsy sale alerts and local listing links from the marketplace hub", () => {
    useQuery.mockReturnValue({ data: { sales: [
      { id: "etsy-sale", marketplace: "etsy", title: "Etsy wool coat", conversationId: "coat", listedElsewhere: ["ebay"] },
      { id: "ebay-sale", marketplace: "ebay", title: "eBay-only sale", conversationId: "other", listedElsewhere: [] },
    ] } });
    const html = renderToStaticMarkup(<EtsySaleAlerts onOpenListing={vi.fn()} />);
    expect(html).toContain("Etsy wool coat");
    expect(html).not.toContain("eBay-only sale");
    expect(html).toContain("You choose any delisting separately.");
    expect(html).toContain("Open item");
  });

  it("refreshes Etsy alerts by reconciling Etsy sales against saved inventory", async () => {
    const reconcile = vi.spyOn(api.marketplaceSales, "reconcile").mockResolvedValue({ matched: 2, unmatched: 1 });
    try {
      useMutation.mockClear();
      renderToStaticMarkup(<EtsySaleAlerts onOpenListing={vi.fn()} />);
      await useMutation.mock.calls[0][0].mutationFn();
      expect(reconcile).toHaveBeenCalledWith("etsy");
    } finally {
      reconcile.mockRestore();
    }
  });

  it("reports newly matched sales and keeps reconciliation errors visible", () => {
    useMutation.mockReturnValueOnce({ mutate: vi.fn(), isPending: false, data: { matched: 2, unmatched: 1 } });
    expect(renderToStaticMarkup(<EtsySaleAlerts onOpenListing={vi.fn()} />)).toContain("2 previously unmatched sales are");
    useMutation.mockReturnValueOnce({ mutate: vi.fn(), isPending: false, error: new Error("Could not refresh sale alerts.") });
    expect(renderToStaticMarkup(<EtsySaleAlerts onOpenListing={vi.fn()} />)).toContain("Could not refresh sale alerts.");
  });

  it("lists orders with fulfillment status, local pagination, and an Etsy orders link", () => {
    const html = renderToStaticMarkup(<EtsyOrders scope="shop-1" period={period} />);
    expect(html).toContain("Vintage wool coat");
    expect(html).toContain("Needs shipping");
    expect(html).toContain("48 matching orders");
    expect(html).toContain("Page 1");
    expect(html).toContain(">Next</button>");
    expect(html).toContain("https://www.etsy.com/your/orders/sold");
    expect(html).not.toContain("Issue refund");
  });

  it("lists active stock, prices in its currency, and the existing eligibility report", () => {
    const html = renderToStaticMarkup(<EtsyListings scope="shop-1" onOpenListing={vi.fn()} />);
    expect(html).toContain("Vintage jacket");
    expect(html).toContain("€50.00");
    expect(html).toContain('aria-label="Etsy listing state"');
    expect(html).toContain("https://www.etsy.com/listing/100");
    expect(html).toContain("Not allowed on Etsy");
    expect(html).not.toContain(">Publish<");
  });
});

describe("etsyMoney", () => {
  it("formats currency divisors supplied by the API and preserves unknown amounts", () => {
    expect(etsyMoney({ amount: "2.5", currency: "KWD" })).toContain("2.500");
    expect(etsyMoney({ amount: "500", currency: "JPY" })).toBe("¥500");
    expect(etsyMoney(null)).toBe("Not available");
    expect(etsyMoney({ amount: "", currency: "USD" })).toBe("Not available");
  });
});
