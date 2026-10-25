import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MarketplaceListings } from "../api/marketplaceListings";
import type { NativeDrafts } from "../api/nativeMarketplaces";
import { MarketplacesSection, deletable, rowStatus } from "./MarketplacesSection";

const { useQuery, useMutation } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({
  useQuery, useMutation, useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn() }),
}));

const SELLWILD = { categories: [{ id: 272, label: "Fashion > Coats & Jackets" }], conditions: [{ id: 2, label: "Gently Used" }],
  suggested: { category_id: 272, condition_id: 2 } };
const live = { url: "https://www.mercari.com/us/item/m70565754838", listing_id: "m70565754838", source: "vendoo", sold_here: false, state: "live" } as const;

function render(drafts: NativeDrafts, listings: MarketplaceListings = {}, { error = null as Error | null, pending = false, etsyEligible = true,
  etsyReady = false, suggested = SELLWILD.suggested as { category_id: number | null; condition_id: number | null }, selected = "" } = {}) {
  useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) =>
    queryKey[0] === "native-marketplaces" ? { data: drafts, error, isError: Boolean(error), isPending: false }
    : queryKey[0] === "marketplace-listings" ? { data: listings, error: null, isPending: false }
    : queryKey[0] === "sellwild-options" ? { data: { ...SELLWILD, suggested }, isPending: false }
    : queryKey[0] === "etsy-listing-defaults" ? { data: etsyReady ? { shipping_profile_id: "1", readiness_state_id: "2" } : {}, isSuccess: true, isPending: false }
    : { data: { shipping: [], processing: [] }, isPending: false, error: null });
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: pending, error: null, variables: { marketplace: "depop", kind: "draft" } });
  return renderToStaticMarkup(<MarketplacesSection convId="item" revisionId="current" title="Tee" disabled={false} etsyEligible={etsyEligible} selected={selected} />);
}

function row(html: string, name: string) {
  const start = html.indexOf(`<li aria-label="${name}"`);
  return html.slice(start, html.indexOf("</li>", start));
}
const disabledButton = (html: string, label: string) => new RegExp(`<button[^>]*disabled=""[^>]*>${label}</button>`).test(html);
const enabledButton = (html: string, label: string) => new RegExp(`<button(?![^>]*disabled="")[^>]*>${label}</button>`).test(html);

describe("marketplaces section", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows one row per marketplace in a fixed order with a status", () => {
    const html = render({});
    const names = [...html.matchAll(/<li aria-label="([^"]+)"/g)].map((match) => match[1]);
    expect(names).toEqual(["eBay", "Etsy", "Poshmark", "Mercari", "Depop", "Sellwild"]);
    expect(row(html, "eBay")).toContain("Not listed");
    // Ordinary "not listed" stays in the row for assistive tech, not as a second line on every marketplace.
    expect(row(html, "eBay")).not.toContain('role="status"');
  });

  it("puts the selected marketplace's actions beside its form, and lists Poshmark in one step", () => {
    const html = render({}, {}, { selected: "poshmark" });
    expect(row(html, "Poshmark")).toContain('aria-selected="true"');
    expect(enabledButton(html, "List…")).toBe(true);
    expect(html).not.toContain("Save draft");
    expect(html).not.toContain("Publish…");
    expect(row(render({ poshmark: { state: "draft", revision_id: "current", draft_url: "https://poshmark.com/edit-listing/x" } }), "Poshmark")).toContain("Not listed");
  });

  it("lists every marketplace from its form, with no draft to save", () => {
    const mercari = render({}, {}, { selected: "mercari" });
    expect(enabledButton(mercari, "List…")).toBe(true);
    expect(mercari).not.toContain("Save draft");
    expect(mercari).not.toContain("Publish…");
    const saved = render({ mercari: { state: "draft", revision_id: "current", draft_url: "https://www.mercari.com/sell/draft/x/" } }, {}, { selected: "mercari" });
    expect(row(saved, "Mercari")).toContain("Not listed");
    expect(saved).not.toContain("Draft saved");
    expect(saved).not.toContain("Open draft");
    expect(enabledButton(saved, "List…")).toBe(true);
    const stale = render({ depop: { state: "draft", revision_id: "older", draft_url: "https://www.depop.com/sellinghub/drafts/edit/1/" } }, {}, { selected: "depop" });
    expect(row(stale, "Depop")).toContain("Not listed");
    expect(stale).not.toContain("Save changes");
    expect(enabledButton(stale, "List…")).toBe(true);
  });

  it("offers a result check instead of repeating an uncertain write", () => {
    const html = render({ mercari: { state: "unknown", revision_id: "current", error: "Check before trying again." } }, {}, { selected: "mercari" });
    expect(row(html, "Mercari")).toContain("Unconfirmed");
    expect(row(html, "Mercari")).toContain('role="status"');
    expect(html).toContain("Check result");
    expect(html).not.toContain("Publish…");
  });

  it("uses Sellwild's matched category and condition, with Change to override", () => {
    const html = render({}, {}, { selected: "sellwild" });
    expect(html).toContain("Fashion &gt; Coats &amp; Jackets · Gently Used");
    expect(html).toContain(">Change</button>");
    expect(html).not.toContain('aria-label="Sellwild category"');
    expect(enabledButton(html, "List…")).toBe(true);
    const unmatched = render({}, {}, { suggested: { category_id: null, condition_id: 2 }, selected: "sellwild" });
    expect(disabledButton(unmatched, "List…")).toBe(true);
    expect(unmatched).toContain('aria-label="Sellwild category"');
    expect(unmatched).toContain("couldn&#x27;t match");
  });

  it("asks for Etsy's shop defaults in Settings instead of per listing", () => {
    const html = render({}, {}, { selected: "etsy" });
    expect(html).not.toContain("Shipping profile");
    expect(html).toContain("Settings → Connections");
    expect(disabledButton(html, "List…")).toBe(true);
  });

  it("warns when an item may not qualify for Etsy and still offers List", () => {
    const html = render({}, {}, { etsyEligible: false, etsyReady: true, selected: "etsy" });
    expect(row(html, "Etsy")).toContain("Not eligible");
    expect(html).toContain("You can still list this");
    expect(enabledButton(html, "List…")).toBe(true);
  });

  it("offers Update on a live eBay, Etsy, or Poshmark listing", () => {
    const listing = (url: string, listingId: string) => ({ ...live, url, listing_id: listingId });
    for (const [selected, entry] of [
      ["ebay", listing("https://www.ebay.com/itm/123456789012", "123456789012")],
      ["etsy", listing("https://www.etsy.com/listing/123456", "123456")],
      ["poshmark", listing("https://poshmark.com/listing/6adc371572e62463d992ffef", "6adc371572e62463d992ffef")],
    ] as const) {
      const html = render({}, { [selected]: entry }, { selected });
      expect(enabledButton(html, "Update…")).toBe(true);
      expect(html).not.toContain("List…");
    }
    const mercari = render({}, { mercari: live }, { selected: "mercari" });
    expect(mercari).not.toContain("Update…");
    expect(mercari).not.toContain("List…");
  });

  it("offers List on eBay from the same rail", () => {
    const html = render({}, {}, { selected: "ebay" });
    expect(row(html, "eBay")).toContain("Not listed");
    expect(enabledButton(html, "List…")).toBe(true);
  });

  it("gives every marketplace its own actions menu, and offers Delist everywhere only for more than one listing", () => {
    const html = render({}, { mercari: live, ebay: { ...live, url: "https://www.ebay.com/itm/1", listing_id: "1", sold_here: true } });
    expect(row(html, "Mercari")).toContain("Live");
    expect(row(html, "eBay")).toContain("Sold here");
    for (const name of ["eBay", "Etsy", "Poshmark", "Mercari", "Depop", "Sellwild"]) expect(row(html, name)).toContain(`aria-label="${name} actions"`);
    expect(html).not.toContain("Delist everywhere…");
    expect(render({}, { mercari: live, depop: { ...live, url: "https://www.depop.com/products/x/", listing_id: "x" } })).toContain("Delist everywhere…");
  });

  it("offers no new listings once the item sold somewhere", () => {
    const sold = { poshmark: { ...live, url: "https://poshmark.com/listing/x", listing_id: "x", sold_here: true } };
    expect(render({}, sold, { selected: "mercari" })).not.toContain("List…");
    expect(render({}, sold, { selected: "sellwild" })).not.toContain("List…");
  });

  it("shows a failed delist with its reason", () => {
    const html = render({}, { mercari: { ...live, state: "failed", error: "Mercari asked to sign in again." } }, { selected: "mercari" });
    expect(row(html, "Mercari")).toContain("Not delisted");
    expect(html).toContain("Mercari asked to sign in again.");
  });

  it("blocks writes when the saved state could not be loaded or another action runs", () => {
    for (const options of [{ error: new Error("Could not load saved marketplace drafts.") }, { pending: true }]) {
      expect(disabledButton(render({}, {}, { ...options, selected: "mercari" }), "List…")).toBe(true);
      expect(/<button[^>]*disabled=""[^>]*>List(?:ing)?…<\/button>/.test(render({}, {}, { ...options, selected: "poshmark" }))).toBe(true);
    }
  });

  it("derives removal wording per marketplace", () => {
    expect(rowStatus("ebay", { ...live, state: "deleted" }, undefined, "current", true).label).toBe("Ended");
    expect(rowStatus("sellwild", { ...live, state: "deleted" }, undefined, "current", true).label).toBe("Delisted");
    expect(deletable({ ...live, state: "failed" })).toBe(true);
    expect(deletable({ ...live, sold_here: true })).toBe(false);
  });
});
