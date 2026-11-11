import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MarketplaceListings } from "../api/marketplaceListings";
import type { ListingData } from "../api/types";
import type { MissingField, NativeDraft, NativeDrafts, PreflightGap } from "../api/nativeMarketplaces";
import { MarketplacesLayout, MarketplacesSection, askChatPrompt, deletable, listChoice, reconnectText, rowStatus, saidBesideList } from "./MarketplacesSection";
import { readyMarketplaces } from "./ListEverywhereDialog";
import { waitlist } from "./MarketplaceActivity";

import { StructuredEditor } from "./ListingEditor";

const { useQuery, useMutation } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn() }));
vi.mock("@tanstack/react-query", async original => ({
  ...await original<typeof import("@tanstack/react-query")>(),
  useQuery, useMutation, useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn() }),
}));

const live = { url: "https://www.mercari.com/us/item/m70565754838", listing_id: "m70565754838", source: "vendoo", sold_here: false, state: "live" } as const;

/** The server always sends ``missing``; fixtures about other things leave it out. */
function withMissing(preflight?: { marketplaces: Record<string, { ready: boolean; gaps: PreflightGap[]; missing?: MissingField[] }> }) {
  return preflight && { ...preflight, marketplaces: Object.fromEntries(Object.entries(preflight.marketplaces)
    .map(([id, check]) => [id, { ...check, missing: check.missing ?? [] }])) };
}

function render(drafts: NativeDrafts, listings: MarketplaceListings = {}, { error = null as Error | null, pending = false, etsyEligible = true,
  etsyReady = false, selected = "", notes = undefined as string[] | undefined,
  onAskChat = undefined as ((text: string) => void) | undefined, onEditField = undefined as ((field: string) => void) | undefined,
  preflight = { marketplaces: {} } as { marketplaces: Record<string, { ready: boolean; gaps: PreflightGap[]; missing?: MissingField[] }> } | undefined,
  preflightMissing = false, preflightPlaceholder = false, preflightError = false,
  nativeFields = [] as { key: string; path: string; label: string; value: string; required: boolean; options: string[] }[],
  listing = {} as ListingData, agentLocks = [] as string[] } = {}) {
  useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) =>
    queryKey[0] === "native-marketplaces" ? { data: drafts, error, isError: Boolean(error), isPending: false }
    : queryKey[0] === "native-editor-form" ? { data: { marketplace: selected, native: true, fields: nativeFields, notes: notes ?? [] } }
    : queryKey[0] === "marketplace-listings" ? { data: listings, error: null, isPending: false }
    : queryKey[0] === "etsy-listing-defaults" ? { data: etsyReady ? { shipping_profile_id: "1", readiness_state_id: "2", return_policy_id: "3" } : {}, isSuccess: true, isPending: false }
    : queryKey[0] === "native-preflight" ? { data: preflightMissing ? undefined : withMissing(preflight), isPending: preflightMissing, isPlaceholderData: preflightPlaceholder, isError: preflightError }
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

  it("omits the persistent Chrome requirement from marketplace actions", () => {
    expect(render({}, {}, { selected: "mercari" })).not.toContain("Requires desktop Chrome with the Bridge extension connected");
    expect(render({}, {}, { selected: "ebay" })).not.toContain("Requires desktop Chrome with the Bridge extension connected");
  });

  it("shows a listing failure after an earlier delist", () => {
    const html = render({ depop: { state: "not_saved", error: "Depop needs a parcel size.", marketplace_message: "parcel_size is required" } },
      { depop: { ...live, state: "deleted" } }, { selected: "depop" });
    expect(html).toContain("Not listed");
    expect(html).toContain("Depop needs a parcel size.");
    expect(html).toMatch(/<details open=""[^>]*>.*Depop said: parcel_size is required/s);
  });

  it("keeps List available when local field checks are incomplete or unavailable", () => {
    for (const options of [
      { preflightMissing: true }, { preflightPlaceholder: true }, { preflightError: true },
      { preflight: { marketplaces: { mercari: { ready: false, gaps: [
        { field: "mercari_specifics.size", message: "Size needs review.", blocking: true },
      ] } } } },
    ]) {
      expect(enabledButton(render({}, {}, { selected: "mercari", ...options }), "List…")).toBe(true);
    }
  });

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

  it.each(["placeholder", "error"])("hides previous revision field warnings during %s checks while allowing List", (state) => {
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
    expect(enabledButton(html, "List…")).toBe(true);
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
    expect(enabledButton(html, "List…")).toBe(true);
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

  it("allows List while the current revision is being checked", () => {
    for (const state of [{ preflightMissing: true }, { preflightPlaceholder: true }, { preflightError: true }]) {
      const html = render({}, {}, { selected: "mercari", ...state });
      expect(enabledButton(html, "List…")).toBe(true);
    }
  });

  it("shows one row per marketplace in a fixed order with a status", () => {
    const html = render({});
    const names = [...html.matchAll(/<li aria-label="([^"]+)"/g)].map((match) => match[1]);
    expect(names).toEqual(["eBay", "Etsy", "Poshmark", "Mercari", "Depop"]);
    expect(html).not.toContain("Facebook");
    expect(row(html, "eBay")).toContain("Not listed");
    // Ordinary "not listed" stays in the row for assistive tech, not as a second line on every marketplace.
    expect(row(html, "eBay")).not.toContain('role="status"');
  });

  it("puts the selected marketplace's actions beside its form, and lists Poshmark in one step", () => {
    const html = render({}, {}, { selected: "poshmark" });
    expect(row(html, "Poshmark")).toContain('aria-current="true"');
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
  });


  it("asks for Etsy's shop defaults in Settings instead of per listing", () => {
    const html = render({}, {}, { selected: "etsy" });
    expect(html).not.toContain("Shipping profile");
    expect(html).toContain("Settings → Connections");
    expect(enabledButton(html, "List…")).toBe(true);
  });

  it("warns about Etsy eligibility once, without disabling List", () => {
    const message = "Etsy generally requires vintage (20+ years), handmade or craft supplies. If it is vintage or handmade, set When made.";
    const html = render({}, {}, { etsyEligible: false, etsyReady: true, selected: "etsy",
      preflight: { marketplaces: { etsy: { ready: false, gaps: [{ field: "etsy_specifics.when_made", message, blocking: true }] } } } });
    // Short enough for the narrow rail, and named in full by the row's tooltip.
    expect(row(html, "Etsy")).toContain(">May not qualify<");
    expect(row(html, "Etsy")).toContain('title="May not qualify"');
    expect(html.split("Etsy generally requires").length - 1).toBe(1);
    expect(enabledButton(html, "List…")).toBe(true);
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

  it("keeps List open on another marketplace while one is listing", () => {
    const html = render({ mercari: { state: "publishing", running: true } }, {}, { selected: "poshmark" });
    expect(row(html, "Mercari")).toContain("Listing…");
    expect(enabledButton(html, "List…")).toBe(true);
  });

  it("shows a List that waits for a busy marketplace as Waiting, with Cancel instead of List", () => {
    waitlist.add({ convId: "item", title: "Tee", marketplace: "depop", kind: "list", revisionId: "current" });
    try {
      const html = render({}, {}, { selected: "depop" });
      expect(row(html, "Depop")).toContain("Waiting for Depop");
      expect(html).toContain("List starts when Depop finishes its current action.");
      expect(enabledButton(html, "Cancel")).toBe(true);
      expect(html).not.toContain("List…");
      expect(html).not.toContain('role="alert"');
    } finally {
      for (const item of waitlist.snapshot()) waitlist.remove(item.id);
    }
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
    // Depop's shipping refusal opens the Package size row, the form's own name for it.
    const shipping = render({ depop: { state: "draft", error: "Depop needs shipping.", error_code: "missing_fields",
      error_fields: [{ field: "depop_specifics.parcelSize", message: "Depop needs shipping" }] } },
      {}, { selected: "depop", onEditField: vi.fn() });
    expect(enabledButton(shipping, "Edit Package size")).toBe(true);
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

  it("keeps the marketplace's raw words under Details and offers chat only for what chat can fix", () => {
    const refused = render({ etsy: { state: "not_saved", error: "Etsy needs to know who made this item.", error_code: "missing_fields",
      error_fields: [{ field: "etsy_specifics.who_made", message: "Etsy needs to know who made this item." }],
      marketplace_message: "Invalid value () for enum(i_did, someone_else, collective)" } }, {}, { selected: "etsy", etsyReady: true, onAskChat: vi.fn() });
    expect(refused).toMatch(/<details open=""[^>]*>.*<summary[^>]*>.*Details.*<\/summary>.*Etsy said: Invalid value/s);
    expect(refused.indexOf("<details")).toBeLessThan(refused.indexOf("Etsy said:"));
    expect(refused.match(/Etsy said:/g)).toHaveLength(1);
    expect(enabledButton(refused, "Ask chat")).toBe(true);
    for (const error_code of ["network", "signed_out", "rate_limited", "uncertain"] as const) {
      const html = render({ etsy: { state: "not_saved", error: "Etsy is having trouble right now.", error_code } }, {},
        { selected: "etsy", etsyReady: true, onAskChat: vi.fn() });
      expect(html, String(error_code)).not.toContain(">Ask chat</button>");
    }
    const prompt = askChatPrompt("etsy", { state: "not_saved", error: "Etsy needs to know who made this item.", error_code: "missing_fields" });
    expect(prompt).toContain("plain words");
  });

  it("says what a reconnect found instead of leaving the last refusal as the answer", () => {
    expect(reconnectText("Mercari", { ok: true, signed_in: true, state: "signed_in", label: "betwixtandbetween" }))
      .toBe("Mercari is connected as betwixtandbetween. Press List… again to list this item.");
    expect(reconnectText("Mercari", { ok: true, signed_in: false, state: "signed_out" }))
      .toContain("Mercari still needs a sign-in");
    expect(reconnectText("Mercari", undefined)).toBeNull();
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

  it("offers List again on a marketplace the seller delisted, without Update or Delist", () => {
    const deleted = { ...live, state: "deleted" } as const;
    // Items delisted before the record was reset still read published.
    for (const drafts of [{}, { mercari: { state: "published", revision_id: "current", listing_url: live.url } }] as NativeDrafts[]) {
      const html = render(drafts, { mercari: deleted }, { selected: "mercari" });
      expect(row(html, "Mercari")).toContain("Delisted");
      expect(enabledButton(html, "List…")).toBe(true);
      expect(html).not.toContain(">Update…</button>");
    }
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
      ["depop", listing("https://www.depop.com/products/x/", "x")],
    ] as const) {
      const html = render({ [selected]: { state: "published", revision_id: "older" } } as NativeDrafts, { [selected]: entry }, { selected });
      expect(enabledButton(html, "Update…")).toBe(true);
      expect(html).not.toContain("List…");
    }
  });

  it("shows missing fields as advice without stopping List", () => {
    const gaps = [{ field: "ebay_specifics.category_specifics.Color", message: "eBay needs Color.", blocking: true }];
    const html = render({}, {}, {
      selected: "ebay", onAskChat: vi.fn(), onEditField: vi.fn(),
      preflight: { marketplaces: { ebay: { ready: false, gaps } } },
    });
    expect(html).toContain("eBay needs Color.");
    expect(enabledButton(html, "Edit Color")).toBe(true);
    expect(enabledButton(html, "Ask chat")).toBe(true);
    expect(enabledButton(html, "List…")).toBe(true);
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
    for (const name of ["eBay", "Etsy", "Poshmark", "Mercari", "Depop"]) expect(row(html, name)).toContain(`aria-label="${name} actions"`);
    expect(html).not.toContain("Delist everywhere…");
    expect(render({}, { mercari: live, depop: { ...live, url: "https://www.depop.com/products/x/", listing_id: "x" } })).toContain("Delist everywhere…");
  });

  it("offers List everywhere for the marketplaces that are ready, and names them", () => {
    const html = render({}, { mercari: live }, { etsyReady: true });
    expect(html).toContain("List everywhere…");
    expect(html).toContain('title="List on eBay, Etsy, Poshmark, Depop"');
    const blocked = render({}, {}, { preflight: { marketplaces: { ebay: { ready: false, gaps: [{ field: "price", message: "eBay needs a price.", blocking: true }] } } } });
    expect(blocked).toContain('title="List on eBay, Etsy, Poshmark, Mercari, Depop"');
    const sold = { poshmark: { ...live, url: "https://poshmark.com/listing/x", listing_id: "x", sold_here: true } };
    expect(render({}, sold)).not.toContain("List everywhere…");
  });

  it("makes List everywhere the rail's one primary action and keeps Delist everywhere apart from it, last", () => {
    // Two live listings and three ready marketplaces: both listing-wide actions show.
    const html = render({}, { mercari: live, depop: { ...live, url: "https://www.depop.com/products/x/", listing_id: "x" } }, { etsyReady: true });
    const button = (label: string) => html.match(new RegExp(`<button[^>]*>(?:(?!</button>).)*${label}(?:(?!</button>).)*</button>`))?.[0] ?? "";
    expect(button("List everywhere…")).toContain("bg-primary");
    for (const label of ["Record a sale…", "Delist everywhere…"]) expect(button(label)).not.toContain("bg-primary");
    const order = ["List everywhere…", "Record a sale…", "Delist everywhere…"].map((label) => html.indexOf(label));
    expect(order).toEqual([...order].sort((a, b) => a - b));
    // A divider stands between the other actions and Delist.
    const between = html.slice(html.indexOf("Record a sale…"), html.indexOf("Delist everywhere…"));
    expect(between).toMatch(/<div class="[^"]*border-t/);
  });

  it("says on every tab not listed yet whether it is ready, or how many fields the server says it needs", () => {
    const html = render({}, { mercari: live }, { preflight: { marketplaces: {
      ebay: { ready: false, gaps: [
        { field: "price", message: "eBay needs a price.", blocking: true },
        { field: "ebay_specifics.category_specifics.Color", message: "Color is required.", blocking: false, required: true, label: "Color" },
        { field: "marketplace_photos.ebay", message: "One photo is left off.", blocking: false },
      ], missing: [
        { field: "price", label: "Price", message: "eBay needs a price." },
        { field: "ebay_specifics.category_specifics.Color", label: "Color", message: "Color is required." },
      ] },
      poshmark: { ready: true, gaps: [{ field: "marketplace_photos.poshmark", message: "One photo is left off.", blocking: false }] },
      depop: { ready: true, gaps: [] },
    } } });
    expect(row(html, "eBay")).toContain("2 to fill");
    expect(row(html, "eBay")).toContain('title="Needs 2 fields: Price, Color"');
    expect(row(html, "Poshmark")).toContain("Ready");
    expect(row(html, "Depop")).toContain("Ready");
    // A live listing keeps its own state; an unchecked one says nothing new.
    expect(row(html, "Mercari")).toContain("Listed");
    expect(row(html, "Mercari")).not.toContain("Ready");
    expect(render({}, {}, { preflightPlaceholder: true, preflight: { marketplaces: { depop: { ready: true, gaps: [] } } } })).not.toContain("Ready");
  });

  it("describes each marketplace in List everywhere's picker, and only ready ones are picked by Select all ready", () => {
    const idle = rowStatus("ebay", undefined, undefined, "current", true);
    const base = { status: idle, canAct: true, listable: true, retryIn: 0, soldSomewhere: false, check: { ready: true, gaps: [], missing: [] } };
    const ready = listChoice("ebay", base);
    const needs = listChoice("depop", { ...base, check: { ready: false, gaps: [{ field: "size", message: "Size", blocking: true }],
      missing: [{ field: "size", label: "Size", message: "Size" }, { field: "price", label: "Price", message: "Price" }] } });
    const listed = listChoice("mercari", { ...base, status: rowStatus("mercari", live, undefined, "current", true), listable: false });
    const sold = listChoice("poshmark", { ...base, status: rowStatus("poshmark", { ...live, sold_here: true }, undefined, "current", true), listable: false });
    const waiting = listChoice("etsy", { ...base, retryIn: 12 });
    expect(ready).toMatchObject({ label: "Ready", tone: "ready", selectable: true });
    expect(needs).toMatchObject({ label: "Needs 2 fields", tone: "needs", selectable: true });
    expect(needs.missing?.map((item) => item.label)).toEqual(["Size", "Price"]);
    expect(listed).toMatchObject({ label: "Listed", selectable: false });
    expect(sold).toMatchObject({ label: "Sold", selectable: false });
    expect(waiting).toMatchObject({ label: "Retry in 12s", selectable: false });
    expect(listChoice("ebay", { ...base, canAct: false }).selectable).toBe(false);
    expect(listChoice("ebay", { ...base, soldSomewhere: true }).selectable).toBe(false);
    expect(listChoice("ebay", { ...base, check: null })).toMatchObject({ label: "Not checked", selectable: true });
    expect(readyMarketplaces([ready, needs, listed, sold, waiting])).toEqual(["ebay"]);
  });

  it("sets the marketplace where the item sold apart in its own color", () => {
    const sold = { ebay: { ...live, url: "https://www.ebay.com/itm/1", listing_id: "1", sold_here: true }, mercari: live };
    expect(rowStatus("ebay", sold.ebay, undefined, null, true).tone).toBe("sold");
    const html = render({}, sold, { selected: "ebay" });
    expect(row(html, "eBay")).toContain("text-info-foreground");
    expect(row(html, "Mercari")).not.toContain("text-info-foreground");
    expect(html).toMatch(/data-marketplace-pane="ebay" data-tone="sold" class="[^"]*bg-info\/4/);
  });

  it("offers no new listings once the item sold somewhere", () => {
    const sold = { poshmark: { ...live, url: "https://poshmark.com/listing/x", listing_id: "x", sold_here: true } };
    expect(render({}, sold, { selected: "mercari" })).not.toContain("List…");
    expect(render({}, sold, { selected: "depop" })).not.toContain("List…");
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

  it("shows Delisted after a delist, Listed while live, and Not listed when never listed", () => {
    expect(rowStatus("ebay", undefined, undefined, "current", true).label).toBe("Not listed");
    expect(rowStatus("mercari", live, undefined, "current", true).label).toBe("Listed");
    expect(rowStatus("ebay", { ...live, state: "deleted" }, undefined, "current", true).label).toBe("Delisted");
    // Delisting clears old errors, so an error after it is from a newer List and must show.
    const refused = rowStatus("poshmark", { ...live, state: "deleted" },
      { state: "draft", error: "Poshmark won't turn on Smart Sell at this Lowest Price.", marketplace_message: "Your minimum price is too low" } as NativeDraft,
      "current", true);
    expect(refused).toMatchObject({ tone: "failed", label: "Not listed", detail: "Poshmark won't turn on Smart Sell at this Lowest Price." });
    expect(refused.said).toBe("Poshmark said: Your minimum price is too low");
    expect(rowStatus("mercari", { ...live, state: "deleted" }, undefined, "current", true).label).toBe("Delisted");
    expect(rowStatus("depop", { ...live, state: "deleted", url: "https://www.depop.com/products/x/", listing_id: "x" }, undefined, "current", true).label).toBe("Delisted");
    expect(render({}, { depop: { ...live, url: "https://www.depop.com/products/x/", listing_id: "x" } })).not.toContain("Delist everywhere…");
    expect(render({}, { mercari: live, depop: { ...live, url: "https://www.depop.com/products/y/", listing_id: "y" } })).toContain("Delist everywhere…");
    expect(deletable({ ...live, state: "failed" })).toBe(true);
    expect(deletable({ ...live, sold_here: true })).toBe(false);
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
