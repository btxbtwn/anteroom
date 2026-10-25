import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MarketplaceListings } from "../api/marketplaceListings";
import { DelistActions, deletable } from "./DelistActions";

const { useQuery, useMutation } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useQuery, useMutation, useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn() }) }));

const live = { url: "https://www.mercari.com/us/item/m70565754838", listing_id: "m70565754838", source: "vendoo", sold_here: false, state: "live" } as const;

function render(data: MarketplaceListings) {
  useQuery.mockReturnValue({ data, isPending: false, error: null });
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false, error: null });
  return renderToStaticMarkup(<DelistActions convId="item" title="Tee" disabled={false} />);
}

describe("delist controls", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows nothing when the item has no marketplace listings", () => {
    expect(render({})).toBe("");
  });

  it("offers each live listing and never the one that sold", () => {
    const html = render({ mercari: live, ebay: { ...live, url: "https://www.ebay.com/itm/377507697747", listing_id: "377507697747", sold_here: true } });
    expect(html).toContain("Sold here");
    expect(html).toMatch(/aria-label="Delist from eBay"[^>]*disabled|disabled[^>]*aria-label="Delist from eBay"/);
    expect(html).toContain("Delist everywhere…");
    expect(deletable({ ...live, sold_here: true })).toBe(false);
    expect(deletable({ ...live, state: "deleted" })).toBe(false);
    expect(deletable({ ...live, state: "failed" })).toBe(true);
  });

  it("shows a failed delist with its reason", () => {
    const html = render({ mercari: { ...live, state: "failed", error: "Mercari asked to sign in again." } });
    expect(html).toContain("Not delisted");
    expect(html).toContain("Mercari asked to sign in again.");
  });
});
