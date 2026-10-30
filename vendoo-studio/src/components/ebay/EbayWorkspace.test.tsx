import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EbayWorkspace } from "./EbayWorkspace";

const { useQuery, useMutation, tab } = vi.hoisted(() => ({
  useQuery: vi.fn(),
  useMutation: vi.fn(),
  tab: { current: "Offers", allEnabled: false },
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery,
  useMutation,
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock("./FormDrafts", () => ({
  useFormDraft: () => [tab.current, vi.fn()],
}));
vi.mock("./SellerPanels", () => ({
  ActivityPanel: () => null,
  DashboardPanel: () => <div>Dashboard panel</div>,
  InboxPanel: () => null,
  OrdersPanel: () => null,
}));
vi.mock("./MarketingPanel", () => ({ MarketingPanel: () => null }));
vi.mock("./ListingsPanel", () => ({ ListingsPanel: () => null, ResearchPanel: () => null }));
vi.mock("../SaleCalendar", () => ({ SaleCalendar: () => null }));

beforeEach(() => {
  tab.current = "Offers";
  tab.allEnabled = false;
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false, error: null, reset: vi.fn() });
  useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) => {
    if (queryKey[0] === "direct-connections") {
      return {
        data: { accounts: { ebay: { connected: true, label: "Ada", connectedAt: 1, pending: false, authorizeUrl: null, error: null } } },
        isPending: false,
        error: null,
      };
    }
    if (queryKey[0] === "ebay-offers") {
      return {
        data: { connected: true, listings: [], history: [], checkIntervalMinutes: 30, lastCheck: null, lastError: null,
          allListings: { enabled: tab.allEnabled, discountPercent: 15, minimumPrice: "" } },
        isPending: false,
        error: null,
      };
    }
    return { data: undefined, isPending: false, error: null };
  });
});

describe("eBay offers", () => {
  it("shows offer controls on the eBay Offers tab", () => {
    const html = renderToStaticMarkup(<EbayWorkspace onOpenListing={vi.fn()} />);
    expect(html).toContain("Offers to interested buyers");
    expect(html).toContain("Automatic offers check every 30 minutes");
    expect(html).toContain("No unsold listings with eBay links");
    expect(html).toContain("All current and future listings");
  });

  it("restores all-listings scope and its saved discount without selecting items", () => {
    tab.allEnabled = true;
    const html = renderToStaticMarkup(<EbayWorkspace onOpenListing={vi.fn()} />);
    expect(html).toContain("All current and future listings: 15% off");
    expect(html).toContain("no minimum price");
    expect(html).toContain('aria-label="Discount for all eBay listings"');
    expect(html).toContain('value="15"');
    expect(html).toContain("Enable offers for all current and future listings");
    expect(html).not.toContain("Preview selected listings");
    expect(html).not.toContain("No unsold listings with eBay links");
  });

  it("keeps the offer controls off the other eBay tabs", () => {
    tab.current = "Dashboard";
    const html = renderToStaticMarkup(<EbayWorkspace onOpenListing={vi.fn()} />);
    expect(html).toContain("Dashboard panel");
    expect(html).toContain(">Offers<");
    expect(html).not.toContain("Automatic offers check every 30 minutes");
  });
});
