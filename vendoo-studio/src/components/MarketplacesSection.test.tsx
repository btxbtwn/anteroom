import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MarketplaceListings } from "../api/marketplaceListings";
import type { ListingData } from "../api/types";
import type { NativeDraft, NativeDrafts } from "../api/nativeMarketplaces";
import { MarketplacesLayout, MarketplacesSection, askChatPrompt, deletable, facebookCheckText, reconnectText, rowStatus, saidBesideList } from "./MarketplacesSection";

import { StructuredEditor } from "./ListingEditor";

const { useQuery, useMutation } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn() }));
vi.mock("@tanstack/react-query", async original => ({
  ...await original<typeof import("@tanstack/react-query")>(),
  useQuery, useMutation, useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn() }),
}));

const live = { url: "https://www.mercari.com/us/item/m70565754838", listing_id: "m70565754838", source: "vendoo", sold_here: false, state: "live" } as const;

function render(drafts: NativeDrafts, listings: MarketplaceListings = {}, { error = null as Error | null, pending = false, etsyEligible = true,
  etsyReady = false, selected = "", notes = undefined as string[] | undefined,
  onAskChat = undefined as ((text: string) => void) | undefined, onEditField = undefined as ((field: string) => void) | undefined,
  preflight = { marketplaces: {} } as { marketplaces: Record<string, { ready: boolean; gaps: { field: string; message: string; blocking: boolean }[] }> } | undefined,
  preflightMissing = false, preflightPlaceholder = false, preflightError = false,
  nativeFields = [] as { key: string; path: string; label: string; value: string; required: boolean; options: string[] }[],
  listing = {} as ListingData, agentLocks = [] as string[] } = {}) {
  useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) =>
    queryKey[0] === "native-marketplaces" ? { data: drafts, error, isError: Boolean(error), isPending: false }
    : queryKey[0] === "native-editor-form" ? { data: { marketplace: selected, native: true, fields: nativeFields, notes: notes ?? [] } }
    : queryKey[0] === "marketplace-listings" ? { data: listings, error: null, isPending: false }
    : queryKey[0] === "etsy-listing-defaults" ? { data: etsyReady ? { shipping_profile_id: "1", readiness_state_id: "2", return_policy_id: "3" } : {}, isSuccess: true, isPending: false }
    : queryKey[0] === "native-preflight" ? { data: preflightMissing ? undefined : preflight, isPending: preflightMissing, isPlaceholderData: preflightPlaceholder, isError: preflightError }
    : { data: { shipping: [], processing: [] }, isPending: false, error: null });
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: pending, error: null, variables: { marketplace: "depop", kind: "draft" } });
  if (notes) return renderToStaticMarkup(
    <MarketplacesLayout convId="item" revisionId="current" title="Tee" disabled={false} etsyEligible={etsyEligible}
      ids={["general", "mercari"]} selected={selected} onSelect={() => {}} onEditField={onEditField} onAskChat={onAskChat}>
      <StructuredEditor convId="item" listing={listing} tab={selected} target={null} onChange={() => {}} onAskChat={onAskChat} agentLocks={agentLocks} />
    </MarketplacesLayout>,
  );
  return renderToStaticMarkup(<MarketplacesSection convId="item" revisionId="current" title="Tee" disabled={false} etsyEligible={etsyEligible}
    selected={selected} onAskChat={onAskChat} onEditField={onEditField} />);
}

function row(html: string, name: string) {
  const start = html.indexOf(`<li aria-label="${name}"`);
  return html.slice(start, html.indexOf("</li>", start));
}
const disabledButton = (html: string, label: string) => new RegExp(`<button[^>]*disabled=""[^>]*>${label}</button>`).test(html);
const enabledButton = (html: string, label: string) => new RegExp(`<button(?![^>]*disabled="")[^>]*>${label}</button>`).test(html);

describe("marketplaces section", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reloads the editor when a refill saves a new definition for the same category", () => {
    const queryKey = (options: { label: string }) => {
      render({}, {}, { selected: "mercari", notes: [], listing: {
        direct_category_ids: { mercari: "2008" },
        _native_forms: { mercari: { fields: [{ path: "mercari_specifics.shippingClassId", options: [options.label] }] } },
      } });
      return [...useQuery.mock.calls].reverse().find(([arg]) => arg.queryKey[0] === "native-editor-form")?.[0].queryKey;
    };
    const before = queryKey({ label: "Old label" });
    const after = queryKey({ label: "Current label" });
    expect(before.slice(0, 4)).toEqual(after.slice(0, 4));
    expect(before).not.toEqual(after);
  });

  it.each(["placeholder", "error"])("hides previous revision field warnings during %s checks while keeping List blocked", (state) => {
    const message = "Brand: Required field is empty.";
    const options = {
      selected: "mercari", notes: [] as string[],
      preflight: { marketplaces: { mercari: { ready: false, gaps: [
        { field: "mercari_specifics.brand", message, blocking: true },
      ] } } },
    };
    expect(render({}, {}, options)).toContain(message);
    const html = render({}, {}, { ...options,
      preflightPlaceholder: state === "placeholder", preflightError: state === "error",
    });
    expect(html).not.toContain(message);
    expect(disabledButton(html, "List…")).toBe(true);
    const withField = { ...options, nativeFields: [{ key: "mercari_specifics.brand", path: "mercari_specifics.brand",
      label: "Brand", value: "Anne Klein", required: true, options: [] as string[] }] };
    expect(render({}, {}, withField)).toContain(message);
    expect(render({}, {}, { ...withField,
      preflightPlaceholder: state === "placeholder", preflightError: state === "error",
    })).not.toContain(message);
  });

  it("shows a repeated category error once across preflight gaps and form notes, keeping each repair action", () => {
    const message = "That Mercari category has subcategories. Choose the most specific one.";
    const html = render({}, {}, { selected: "mercari", notes: [message, `  ${message}  `, "A separate form note."],
      onEditField: vi.fn(), preflight: { marketplaces: { mercari: { ready: false, gaps: [
        { field: "marketplace_category_ids.mercari", message, blocking: true },
        { field: "direct_category_ids.mercari", message, blocking: true },
        { field: "title", message: "Mercari needs a title.", blocking: true },
      ] } } },
    });
    expect(html.split(message)).toHaveLength(2);
    expect(html).toContain("Mercari needs a title.");
    expect(html).toContain("A separate form note.");
    expect(html).toContain("Edit Title");
    expect(disabledButton(html, "List…")).toBe(true);
    const standalone = render({}, {}, { selected: "mercari", notes: [message, message] });
    expect(standalone.split(message)).toHaveLength(2);
  });

  it("offers Ask chat once for a category error reported by checks and form notes", () => {
    const message = "That Mercari category has subcategories. Choose the most specific one.";
    const options = { selected: "mercari", notes: [message, message], onAskChat: vi.fn() };
    for (const preflight of [
      { marketplaces: { mercari: { ready: true, gaps: [] } } },
      { marketplaces: { mercari: { ready: false, gaps: [{ field: "marketplace_category_ids.mercari", message, blocking: true }] } } },
    ]) {
      const html = render({}, {}, { ...options, preflight });
      expect(html.split(message)).toHaveLength(2);
      expect(enabledButton(html, "Ask chat")).toBe(true);
      expect(html.match(/>Ask chat<\/button>/g)).toHaveLength(1);
    }
  });

  it("offers Ask chat for category refusals without an error code and changed form schemas", () => {
    for (const error_code of [undefined, "schema_drift"] as const) {
      const html = render({ mercari: { state: "draft", error: "Choose a more specific category.", error_code } }, {},
        { selected: "mercari", onAskChat: vi.fn() });
      expect(enabledButton(html, "Ask chat")).toBe(true);
    }
  });

  it("does not repeat a query error as the selected draft error or a form note", () => {
    const message = "Mercari refused this category.";
    const html = render({ mercari: { state: "draft", error: message, marketplace_message: message } }, {},
      { selected: "mercari", error: new Error(message), notes: [message] });
    expect(html.split(message)).toHaveLength(2);
  });

  it("holds List until the current revision has passed preflight", () => {
    for (const state of [{ preflightMissing: true }, { preflightPlaceholder: true }, { preflightError: true }]) {
      const html = render({}, {}, { selected: "mercari", ...state });
      expect(disabledButton(html, "List…")).toBe(true);
    }
  });

  it("shows one row per marketplace in a fixed order with a status", () => {
    const html = render({});
    const names = [...html.matchAll(/<li aria-label="([^"]+)"/g)].map((match) => match[1]);
    expect(names).toEqual(["eBay", "Etsy", "Poshmark", "Mercari", "Depop", "Sellwild", "Vinted", "Facebook"]);
    expect(row(html, "eBay")).toContain("Not listed");
    // Ordinary "not listed" stays in the row for assistive tech, not as a second line on every marketplace.
    expect(row(html, "eBay")).not.toContain('role="status"');
  });

  it("puts the selected marketplace's actions beside its form, and lists Poshmark in one step", () => {
    const html = render({}, {}, { selected: "poshmark" });
    expect(row(html, "Poshmark")).toContain('aria-selected="true"');
    expect(enabledButton(html, "List…")).toBe(true);
    expect(enabledButton(html, "Save draft")).toBe(true);
    expect(html).not.toContain("Publish…");
    expect(row(render({ poshmark: { state: "draft", revision_id: "current", draft_url: "https://poshmark.com/edit-listing/x" } }), "Poshmark")).toContain("Draft");
  });

  it("saves native private drafts separately from listing and shows unsaved draft edits", () => {
    const mercari = render({}, {}, { selected: "mercari" });
    expect(enabledButton(mercari, "List…")).toBe(true);
    expect(enabledButton(mercari, "Save draft")).toBe(true);
    expect(mercari).not.toContain("Publish…");
    const saved = render({ mercari: { state: "draft", revision_id: "current", draft_url: "https://www.mercari.com/sell/draft/x/" } }, {}, { selected: "mercari" });
    expect(row(saved, "Mercari")).toContain("Draft");
    expect(saved).not.toContain("Draft saved");
    expect(saved).not.toContain("Open draft");
    expect(enabledButton(saved, "List…")).toBe(true);
    const stale = render({ depop: { state: "draft", revision_id: "older", draft_url: "https://www.depop.com/sellinghub/drafts/edit/1/" } }, {}, { selected: "depop" });
    expect(row(stale, "Depop")).toContain("Draft");
    expect(stale).toContain("Changes in Anteroom have not been saved");
    expect(stale).not.toContain("Save changes");
    expect(enabledButton(stale, "List…")).toBe(true);
  });

  it("offers a result check instead of repeating an uncertain write", () => {
    const html = render({ mercari: { state: "unknown", revision_id: "current", error: "Check before trying again." } }, {}, { selected: "mercari" });
    expect(row(html, "Mercari")).toContain("Unconfirmed");
    expect(row(html, "Mercari")).toContain('role="status"');
    expect(html).toContain("Check result");
    expect(html).not.toContain("Publish…");
    const vinted = render({ vinted: { state: "unknown", revision_id: "current", error: "Check before trying again." } }, {}, { selected: "vinted" });
    expect(row(vinted, "Vinted")).toContain("Unconfirmed");
    expect(vinted).toContain("Check result");
    expect(vinted).toContain("Paste the link if one was created");
    expect(vinted).toContain("Check link");
    expect(vinted).not.toContain("does not save a draft");
    expect(vinted).not.toContain("Save draft");
    expect(vinted).not.toContain("Publish…");
    expect(enabledButton(render({}, {}, { selected: "vinted" }), "List…")).toBe(true);
  });

  it("uses the Sellwild form selections and blocks listing when its category is missing", () => {
    const html = render({}, {}, { selected: "sellwild" });
    expect(html).not.toContain(">Change</button>");
    expect(html).not.toContain('aria-label="Sellwild category"');
    expect(enabledButton(html, "List…")).toBe(true);
    const unmatched = render({}, {}, { selected: "sellwild", preflight: { marketplaces: { sellwild: { ready: false,
      gaps: [{ field: "sellwild_specifics.nativeCategoryId", message: "Choose a Sellwild category.", blocking: true }] } } } });
    expect(disabledButton(unmatched, "List…")).toBe(true);
    expect(unmatched).toContain("Choose a Sellwild category.");
  });

  it("asks for Etsy's shop defaults in Settings instead of per listing", () => {
    const html = render({}, {}, { selected: "etsy" });
    expect(html).not.toContain("Shipping profile");
    expect(html).toContain("Settings → Connections");
    expect(disabledButton(html, "List…")).toBe(true);
  });

  it("disables Etsy's List with the reason when the item does not qualify", () => {
    const html = render({}, {}, { etsyEligible: false, etsyReady: true, selected: "etsy" });
    expect(row(html, "Etsy")).toContain("Not eligible");
    expect(html).toContain("Etsy only allows vintage (20+ years), handmade or craft supplies.");
    expect(disabledButton(html, "List…")).toBe(true);
    expect(html).not.toContain("Ask chat");
  });

  it("shows progress and Cancel while a List runs, and offers Check or Reset once it is unconfirmed", () => {
    const running = render({ mercari: { state: "publishing", running: true, progress: { step: "publish", label: "Publishing", index: 1, total: 2 } } },
      {}, { selected: "mercari" });
    expect(row(running, "Mercari")).toContain("Listing…");
    expect(running).toContain("Publishing…");
    expect(enabledButton(running, "Cancel")).toBe(true);
    expect(running).not.toContain("List…");
    const lost = render({ mercari: { state: "unknown", running: false, error: "Chrome has not said whether this Mercari action finished.",
      error_code: "uncertain" } }, {}, { selected: "mercari" });
    expect(enabledButton(lost, "Check result")).toBe(true);
    expect(enabledButton(lost, "I checked it myself…")).toBe(true);
    // A busy state no job is running any more is unconfirmed, not a spinner.
    const orphan = render({ depop: { state: "saving", running: false } }, {}, { selected: "depop" });
    expect(row(orphan, "Depop")).toContain("Unconfirmed");
  });

  it("shows the marketplace's own words and one action per error code", () => {
    const fields = [{ field: "ebay_specifics.category_specifics.Color", message: "eBay needs Color." }];
    const missing = render({ ebay: { state: "not_saved", error: "eBay needs Color.", error_code: "missing_fields", error_fields: fields,
      marketplace_message: "25002: A user error has occurred. Color is missing." } }, {}, { selected: "ebay", onAskChat: vi.fn(), onEditField: vi.fn() });
    expect(missing).toContain("eBay said: 25002: A user error has occurred. Color is missing.");
    expect(enabledButton(missing, "Edit Color")).toBe(true);
    const location = render({ ebay: { state: "not_saved", error: "Choose the inventory location.", error_code: "missing_fields",
      error_fields: [{ field: "settings.ebay.merchant_location_key", message: "Choose the inventory location." }] } },
      {}, { selected: "ebay", onEditField: vi.fn() });
    expect(enabledButton(location, "Edit Inventory location")).toBe(true);
    expect(enabledButton(missing, "Ask chat")).toBe(true);
    const signedOut = render({ etsy: { state: "not_saved", error: "Reconnect Etsy.", error_code: "signed_out" } }, {}, { selected: "etsy", etsyReady: true });
    expect(enabledButton(signedOut, "Reconnect Etsy")).toBe(true);
    const challenge = render({ mercari: { state: "draft", draft_url: "https://www.mercari.com/sell/draft/x/", error: "Finish Mercari's check.",
      error_code: "challenge" } }, {}, { selected: "mercari" });
    expect(enabledButton(challenge, "Open Mercari")).toBe(true);
    const later = new Date(Date.now() + 60_000).toISOString();
    const limited = render({ poshmark: { state: "not_saved", error: "Poshmark is busy.", error_code: "rate_limited", retry_after: later } }, {}, { selected: "poshmark" });
    expect(/<button[^>]*disabled=""[^>]*>Retry in \d+s<\/button>/.test(limited)).toBe(true);
  });

  it("says what a reconnect found instead of leaving the last refusal as the answer", () => {
    expect(reconnectText("Vinted", { ok: true, signed_in: true, state: "signed_in", label: "betwixtandbetween" }))
      .toBe("Vinted is connected as betwixtandbetween. Press List… again to list this item.");
    expect(reconnectText("Vinted", { ok: true, signed_in: false, state: "signed_out" }))
      .toContain("Vinted still needs a sign-in");
    expect(reconnectText("Vinted", undefined)).toBeNull();
  });

  it("asks chat to fix the named fields, not to list", () => {
    const prompt = askChatPrompt("ebay", { state: "not_saved", error: "eBay needs Color.", error_code: "missing_fields",
      marketplace_message: "Color is missing.", error_fields: [{ field: "ebay_specifics.category_specifics.Color", message: "eBay needs Color." }] });
    expect(prompt).toContain("on eBay failed. Anteroom said: eBay needs Color.");
    expect(prompt).toContain("eBay said: Color is missing.");
    expect(prompt).toContain("ebay_specifics.category_specifics.Color (eBay needs Color.)");
    expect(prompt).toContain("Do not list it");
    const deleted = askChatPrompt("depop", { state: "draft", error: "Depop refused the request (HTTP 404).",
      marketplace_message: "Could not find draft with id: [hidden]" });
    expect(deleted).toContain("The saved Depop draft was deleted.");
    expect(deleted).not.toContain("Fill or fix those fields");
  });

  it("shows Update on a live listing only once the listing has changed", () => {
    const sent = (revision_id: string) => ({ mercari: { state: "published", revision_id, listing_url: live.url } }) as NativeDrafts;
    const current = render(sent("current"), { mercari: live }, { selected: "mercari" });
    expect(current).not.toContain(">Update…</button>");
    expect(current).not.toContain("latest edits");
    const changed = render(sent("older"), { mercari: live }, { selected: "mercari" });
    expect(enabledButton(changed, "Update…")).toBe(true);
    expect(changed).toContain("Your latest edits aren&#x27;t on Mercari yet.");
  });

  it("offers Update on a changed live listing on every direct marketplace", () => {
    const listing = (url: string, listingId: string) => ({ ...live, url, listing_id: listingId });
    for (const [selected, entry] of [
      ["ebay", listing("https://www.ebay.com/itm/123456789012", "123456789012")],
      ["etsy", listing("https://www.etsy.com/listing/123456", "123456")],
      ["poshmark", listing("https://poshmark.com/listing/6adc371572e62463d992ffef", "6adc371572e62463d992ffef")],
      ["mercari", live],
      ["vinted", listing("https://www.vinted.com/items/4567890123", "4567890123")],
      ["sellwild", listing("https://sellwild.com/product/123/test", "123")],
    ] as const) {
      const html = render({ [selected]: { state: "published", revision_id: "older" } } as NativeDrafts, { [selected]: entry }, { selected });
      expect(enabledButton(html, "Update…")).toBe(true);
      expect(html).not.toContain("List…");
    }
  });

  it("shows missing fields and stops List before anything is written", () => {
    const gaps = [{ field: "ebay_specifics.category_specifics.Color", message: "eBay needs Color.", blocking: true }];
    const html = render({}, {}, {
      selected: "ebay", onAskChat: vi.fn(), onEditField: vi.fn(),
      preflight: { marketplaces: { ebay: { ready: false, gaps } } },
    });
    expect(html).toContain("eBay needs Color.");
    expect(enabledButton(html, "Edit Color")).toBe(true);
    expect(enabledButton(html, "Ask chat")).toBe(true);
    expect(disabledButton(html, "List…")).toBe(true);
  });

  it("offers Edit and Ask chat for a value the marketplace will leave empty, without stopping List", () => {
    const gaps = [{ field: "ebay_specifics.category_specifics.Size", message: "Size “Huge” is not one of eBay's choices, so it is left empty.", blocking: false }];
    const html = render({}, {}, {
      selected: "ebay", onAskChat: vi.fn(), onEditField: vi.fn(),
      preflight: { marketplaces: { ebay: { ready: true, gaps } } },
    });
    expect(html).toContain("is not one of eBay");
    expect(enabledButton(html, "Edit Size")).toBe(true);
    expect(enabledButton(html, "Ask chat")).toBe(true);
    expect(enabledButton(html, "List…")).toBe(true);
  });

  it("offers List on eBay from the same rail", () => {
    const html = render({}, {}, { selected: "ebay" });
    expect(row(html, "eBay")).toContain("Not listed");
    expect(enabledButton(html, "List…")).toBe(true);
  });

  it("gives every marketplace its own actions menu, and offers Delist everywhere only for more than one listing", () => {
    const html = render({}, { mercari: live, ebay: { ...live, url: "https://www.ebay.com/itm/1", listing_id: "1", sold_here: true } });
    expect(row(html, "Mercari")).toContain("Listed");
    expect(row(html, "eBay")).toContain("Sold here");
    for (const name of ["eBay", "Etsy", "Poshmark", "Mercari", "Depop", "Sellwild", "Vinted"]) expect(row(html, name)).toContain(`aria-label="${name} actions"`);
    expect(html).not.toContain("Delist everywhere…");
    expect(render({}, { mercari: live, depop: { ...live, url: "https://www.depop.com/products/x/", listing_id: "x" } })).toContain("Delist everywhere…");
  });

  it("offers List everywhere for the marketplaces that are ready, and names them", () => {
    const html = render({}, { mercari: live }, { etsyReady: true });
    expect(html).toContain("List everywhere…");
    expect(html).toContain('title="List on eBay, Etsy, Poshmark, Depop, Sellwild, Vinted"');
    const blocked = render({}, {}, { preflight: { marketplaces: { ebay: { ready: false, gaps: [{ field: "price", message: "eBay needs a price.", blocking: true }] } } } });
    expect(blocked).toContain('title="List on Poshmark, Mercari, Depop, Sellwild, Vinted"');
    const sold = { poshmark: { ...live, url: "https://poshmark.com/listing/x", listing_id: "x", sold_here: true } };
    expect(render({}, sold)).not.toContain("List everywhere…");
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

  it("shows an eBay draft as saved on eBay without a link", () => {
    const draft = { state: "draft", action: "save_draft", revision_id: "current" } as NativeDraft;
    const saved = rowStatus("ebay", undefined, draft, "current", true);
    expect([saved.label, saved.href, saved.detail]).toEqual(["Saved on eBay", undefined, "Not listed. Buyers cannot see it until you List."]);
    expect(rowStatus("ebay", undefined, draft, "newer", true).detail).toBe("Changes in Anteroom have not been saved to eBay.");
  });

  it("derives removal wording per marketplace", () => {
    expect(rowStatus("ebay", { ...live, state: "deleted" }, undefined, "current", true).label).toBe("Ended");
    expect(rowStatus("sellwild", { ...live, state: "deleted" }, undefined, "current", true).label).toBe("Delisted");
    expect(rowStatus("vinted", { ...live, state: "deleted", url: "https://www.vinted.com/items/42", listing_id: "42" }, undefined, "current", true).label).toBe("Deleted");
    const vinted = { ...live, url: "https://www.vinted.com/items/42-tee", listing_id: "42" };
    expect(render({}, { vinted })).not.toContain("Delist everywhere…");
    expect(render({}, { mercari: live, vinted })).toContain("Delist everywhere…");
    expect(deletable({ ...live, state: "failed" })).toBe(true);
    expect(deletable({ ...live, sold_here: true })).toBe(false);
  });

  it("shows a linked Facebook listing as live, offers a check, and leaves deleting to Facebook", () => {
    const facebook = { ...live, url: "https://www.facebook.com/marketplace/item/1234567890123456", listing_id: "1234567890123456", can_delist: false };
    const html = render({}, { facebook, mercari: live }, { selected: "facebook" });
    expect(row(html, "Facebook")).toContain("Listed");
    expect(enabledButton(html, "Check on Facebook")).toBe(true);
    expect(html).toContain("Anteroom can&#x27;t delete Facebook listings yet");
    expect(html).not.toContain("List…");
    expect(html).not.toContain("Delist everywhere…");
    expect(deletable(facebook)).toBe(false);
    const unlinked = render({}, {}, { selected: "facebook" });
    expect(unlinked).not.toContain("Check on Facebook");
    expect(unlinked).toContain("Mark as listed");
  });

  it("describes a Facebook check in the seller's words", () => {
    expect(facebookCheckText({ ok: true, state: "sold", owner: "you", sale_recorded: true, listings: {} }))
      .toBe("Marked sold on Facebook. Anteroom recorded the sale.");
    expect(facebookCheckText({ ok: true, state: "live", owner: "other", sale_recorded: false, listings: {} }))
      .toBe("Listed on Facebook. It is on a different Facebook account than the one signed in to Chrome.");
    expect(facebookCheckText({ ok: false, error: "Facebook asked to sign in again.", sale_recorded: false, listings: {} }))
      .toBe("Facebook asked to sign in again.");
  });
});

describe("an answer is said once", () => {
  beforeEach(() => vi.clearAllMocks());

  it("leaves a gap to the form row it is about, and says only the rest beside List", () => {
    const gaps = [
      { field: "photos", message: "Depop keeps 8 photos. The first 8 of 9 are sent.", blocking: false },
      { field: "depop_specifics.parcelSize", message: "Depop needs a parcel size.", blocking: true },
    ];
    const html = render({}, {}, { selected: "depop", onAskChat: vi.fn(), onEditField: vi.fn(),
      preflight: { marketplaces: { depop: { ready: false, gaps } } } });
    // Nothing known about the form yet: both are said here, once each.
    expect(html.match(/Depop keeps 8 photos/g)).toHaveLength(1);
    expect(html.match(/Depop needs a parcel size/g)).toHaveLength(1);
  });
  it("leaves every gap out of this pane once the form says it draws those rows", () => {
    const gaps = [{ field: "photos", message: "Depop keeps 8 photos. The first 8 of 9 are sent.", blocking: false },
      { field: "depop_specifics.parcelSize", message: "Depop needs a parcel size.", blocking: true }];
    expect(saidBesideList(gaps, []).map((gap) => gap.message)).toEqual(gaps.map((gap) => gap.message));
    expect(saidBesideList(gaps, ["photos"]).map((gap) => gap.message)).toEqual(["Depop needs a parcel size."]);
    expect(saidBesideList(gaps, ["photos", "depop_specifics.parcelSize"])).toEqual([]);
  });
});

it("disables only the agent's target row and labels it while other fields stay editable", () => {
  const html = render({}, {}, { selected: "ebay", notes: [], agentLocks: ["ebay_specifics.Size"],
    nativeFields: ["Size", "Color"].map(label => ({ key: `ebay_specifics.${label}`, path: `ebay_specifics.${label}`,
      label, value: "", required: true, options: ["36", "Blue"] })), onAskChat: vi.fn() });
  expect(html).toContain('fieldset disabled="" data-listing-row="ebay_specifics.Size"');
  expect(html).toContain('fieldset data-listing-row="ebay_specifics.Color"');
  expect(html.match(/Agent editing/g)).toHaveLength(1);
  expect(html).toContain('Ask chat');
});
