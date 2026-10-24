import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DirectAccountStatus } from "../api/directConnections";
import { EtsyPage, MarketplacePage } from "./MarketplacePage";

const { useQuery, useMutation } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useQuery, useMutation,
  useQueryClient: () => ({ invalidateQueries: vi.fn() }) }));
vi.mock("./ebay/EbayPage", () => ({ EbayPage: () => <div>Existing eBay workspace</div> }));

const connected: DirectAccountStatus = { connected: true, label: "Test Etsy shop", connectedAt: 1,
  pending: false, authorizeUrl: null, error: null };

beforeEach(() => {
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false, error: null });
  useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) => queryKey[0] === "direct-connections"
    ? { data: { accounts: { ebay: { ...connected, label: "Test eBay seller" }, etsy: connected } }, isPending: false }
    : { data: { sales: [
      { id: "etsy-sale", marketplace: "etsy", title: "Etsy wool coat", conversationId: "coat", listedElsewhere: ["ebay"] },
      { id: "ebay-sale", marketplace: "ebay", title: "eBay-only sale", conversationId: "other", listedElsewhere: [] },
    ] }, isPending: false, isFetching: false });
});

describe("Marketplace hub", () => {
  it("offers eBay and Etsy with their actual connection labels", () => {
    const html = renderToStaticMarkup(<MarketplacePage selected={null} onSelect={vi.fn()} onOpenListing={vi.fn()} onOpenConnections={vi.fn()} />);
    expect(html).toContain('aria-label="Open eBay"');
    expect(html).toContain('aria-label="Open Etsy"');
    expect(html).toContain("Test eBay seller");
    expect(html).toContain("Test Etsy shop");
    expect(html).not.toContain("Existing eBay workspace");
  });

  it("keeps the existing eBay workspace behind the shared marketplace chooser", () => {
    const html = renderToStaticMarkup(<MarketplacePage selected="ebay" onSelect={vi.fn()} onOpenListing={vi.fn()} onOpenConnections={vi.fn()} />);
    expect(html).toContain("Existing eBay workspace");
    expect(html).toContain('aria-label="Marketplace navigation"');
    expect(html).toContain('value="etsy"');
  });

  it("shows only Etsy sale alerts and opens its real listing manager", () => {
    const html = renderToStaticMarkup(<EtsyPage account={connected} onOpenListing={vi.fn()} onOpenConnections={vi.fn()} />);
    expect(html).toContain("Etsy wool coat");
    expect(html).not.toContain("eBay-only sale");
    expect(html).toContain("https://www.etsy.com/your/shops/me/tools/listings");
    expect(html).toContain("You choose any delisting separately.");
  });

  it("keeps connection actions unavailable while the account status is unknown", () => {
    const html = renderToStaticMarkup(<EtsyPage onOpenListing={vi.fn()} onOpenConnections={vi.fn()} />);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Connect Etsy<\/button>/);
  });

  it("offers pending sign-in and cancellation without starting another connection", () => {
    const html = renderToStaticMarkup(<EtsyPage account={{ ...connected, connected: false, pending: true, authorizeUrl: "https://www.etsy.com/oauth/connect" }} onOpenListing={vi.fn()} onOpenConnections={vi.fn()} />);
    expect(html).toContain("Open Etsy sign-in");
    expect(html).toContain("Cancel sign-in");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Connect Etsy<\/button>/);
  });
});
