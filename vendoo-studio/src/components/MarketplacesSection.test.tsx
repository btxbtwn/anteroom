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
  suggested = SELLWILD.suggested as { category_id: number | null; condition_id: number | null } } = {}) {
  useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) =>
    queryKey[0] === "native-marketplaces" ? { data: drafts, error, isError: Boolean(error), isPending: false }
    : queryKey[0] === "marketplace-listings" ? { data: listings, error: null, isPending: false }
    : queryKey[0] === "sellwild-options" ? { data: { ...SELLWILD, suggested }, isPending: false }
    : { data: { shipping: [], processing: [] }, isPending: false, error: null });
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: pending, error: null, variables: { marketplace: "depop", kind: "draft" } });
  return renderToStaticMarkup(<MarketplacesSection convId="item" revisionId="current" title="Tee" disabled={false} etsyEligible={etsyEligible} />);
}

function row(html: string, name: string) {
  return html.split(`aria-label="${name}"`)[1].split("<li")[0];
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
  });

  it("lists Poshmark in one step, with no draft controls", () => {
    const html = row(render({}), "Poshmark");
    expect(enabledButton(html, "List…")).toBe(true);
    expect(html).not.toContain("Save draft");
    expect(html).not.toContain("Publish…");
    expect(row(render({ poshmark: { state: "draft", revision_id: "current", draft_url: "https://poshmark.com/edit-listing/x" } }), "Poshmark")).toContain("Not listed");
  });

  it("keeps draft marketplaces' publish disabled until a current draft is saved", () => {
    expect(disabledButton(row(render({}), "Mercari"), "Publish…")).toBe(true);
    const saved = row(render({ mercari: { state: "draft", revision_id: "current", draft_url: "https://www.mercari.com/sell/draft/x/" } }), "Mercari");
    expect(saved).toContain("Draft saved");
    expect(saved).toContain("Open draft");
    expect(enabledButton(saved, "Publish…")).toBe(true);
    const stale = row(render({ depop: { state: "draft", revision_id: "older", draft_url: "https://www.depop.com/sellinghub/drafts/edit/1/" } }), "Depop");
    expect(stale).toContain("Draft out of date");
    expect(stale).toContain("Save changes");
    expect(disabledButton(stale, "Publish…")).toBe(true);
  });

  it("offers a result check instead of repeating an uncertain write", () => {
    const html = row(render({ mercari: { state: "unknown", revision_id: "current", error: "Check before trying again." } }), "Mercari");
    expect(html).toContain("Unconfirmed");
    expect(html).toContain("Check result");
    expect(html).not.toContain("Publish…");
  });

  it("needs a Sellwild category and condition before saving", () => {
    expect(enabledButton(row(render({}), "Sellwild"), "Save")).toBe(true);
    const html = row(render({}, {}, { suggested: { category_id: null, condition_id: 2 } }), "Sellwild");
    expect(disabledButton(html, "Save")).toBe(true);
    expect(html).toContain("Choose a category and condition to save.");
    expect(html).toContain('aria-label="Sellwild category"');
  });

  it("explains Etsy ineligibility in its own row, with no actions", () => {
    const html = row(render({}, {}, { etsyEligible: false }), "Etsy");
    expect(html).toContain("Not eligible");
    expect(html).not.toContain("Save draft");
  });

  it("shows live listings with a link and delist selection, never for the one that sold", () => {
    const html = render({}, { mercari: live, ebay: { ...live, url: "https://www.ebay.com/itm/1", listing_id: "1", sold_here: true } });
    expect(row(html, "Mercari")).toContain("Live");
    expect(row(html, "Mercari")).toContain('aria-label="Select Mercari to delist"');
    expect(row(html, "eBay")).toContain("Sold here");
    expect(row(html, "eBay")).not.toContain("to delist");
    expect(html).toContain("Delist everywhere…");
    expect(disabledButton(html, "Delist selected…")).toBe(true);
  });

  it("shows a failed delist with its reason", () => {
    const html = row(render({}, { mercari: { ...live, state: "failed", error: "Mercari asked to sign in again." } }), "Mercari");
    expect(html).toContain("Not delisted");
    expect(html).toContain("Mercari asked to sign in again.");
  });

  it("blocks writes when the saved state could not be loaded or another action runs", () => {
    for (const html of [render({}, {}, { error: new Error("Could not load saved marketplace drafts.") }), render({}, {}, { pending: true })]) {
      expect(disabledButton(row(html, "Mercari"), "Save draft")).toBe(true);
      expect(/<button[^>]*disabled=""[^>]*>List(?:ing)?…<\/button>/.test(row(html, "Poshmark"))).toBe(true);
    }
  });

  it("derives removal wording per marketplace", () => {
    expect(rowStatus("ebay", { ...live, state: "deleted" }, undefined, "current", true).label).toBe("Ended");
    expect(rowStatus("sellwild", { ...live, state: "deleted" }, undefined, "current", true).label).toBe("Delisted");
    expect(deletable({ ...live, state: "failed" })).toBe(true);
    expect(deletable({ ...live, sold_here: true })).toBe(false);
  });
});
