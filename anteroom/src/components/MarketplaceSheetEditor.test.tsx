// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { ConversationActivity, ListingDocument, ListingSaveResult, ListingSheetRow, MarketplaceForm } from "../api/types";
import { WorkspaceRuntimeProvider, type WorkspaceRuntime } from "../app/runtime";
import type { Listing } from "./sidebar/listingShelves";
import { MARKETPLACE_SHEET_COLUMNS } from "./marketplaceSheetConfig";
import { DesktopCellPopover, MarketplaceQuickEditSheet } from "./MarketplaceSheetEditor";

import { MarketplaceSheetView } from "./MarketplaceSheetView";
import { readinessKey } from "./useInventoryReadiness";

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: () => ({
    getVirtualItems: () => [{ key: "conv-1", index: 0, start: 0, size: 56 }],
    getTotalSize: () => 56,
  }),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const LOCAL_RUNTIME: WorkspaceRuntime = {
  hosted: false,
  features: new Set(),
  views: new Set(["listings"]),
  marketplaceForms: new Set(["ebay", "poshmark", "mercari", "depop", "etsy"]),
  actionMarketplaces: new Set(["ebay", "poshmark", "mercari", "depop", "etsy"]),
};

const IDLE_ACTIVITY: ConversationActivity = {
  busy: false,
  writing: false,
  items: [],
  message_count: 0,
  last_message_id: null,
  locked_fields: [],
};

const LISTING_ROW: Listing = {
  id: "conv-1",
  title: "Wool cardigan",
  price: 32,
  status: "draft",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  direct_records: {},
};

const READY_SHEET_ROW: ListingSheetRow = {
  revision_id: "rev-1",
  ready: true,
  needs_load: false,
  cells: {
    price: { value: "32", raw: "32", required: true, missing: false, rejected: false, error: "", editable: true },
    "depop_specifics.condition": { value: "Used - Good", raw: "used_good", required: true, missing: false, rejected: false, error: "", editable: true },
  },
  missing: [],
};

const SAMPLE_DOC: ListingDocument = {
  conversation_id: "conv-1",
  current_revision_id: "rev-1",
  revision_count: 1,
  can_send: true,
  listing: {
    title: "Wool cardigan",
    price: 32,
    depop_specifics: { condition: "used_good" },
  },
};

const DEPOP_FORM: MarketplaceForm = {
  marketplace: "depop",
  category_id: "tops",
  known: true,
  fields: [
    {
      key: "price",
      path: "price",
      label: "Price",
      input_type: "number",
      required: true,
      locked: false,
      multi: false,
      selection_only: false,
      options: [],
      value: 32,
    },
    {
      key: "condition",
      path: "depop_specifics.condition",
      label: "Condition",
      required: true,
      locked: false,
      multi: false,
      selection_only: true,
      options: ["Used - Good", "Brand new"],
      option_values: { "Used - Good": "used_good", "Brand new": "brand_new" },
      value: "used_good",
    },
  ],
  notes: [],
};

function mockSaveResult(listing: Record<string, unknown>, revisionId = "rev-2"): ListingSaveResult {
  return {
    ok: true,
    revision_id: revisionId,
    listing,
    etsy_eligible: false,
    validation: { valid: true, can_send: true, errors: [], warnings: [], info: [] },
  };
}

async function flushMicrotasks() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
}

function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("MarketplaceSheetEditor", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.body.innerHTML = "";
  });

  it("writes only depop_specifics.price when editing Price on Depop and flushes draft on Done", async () => {
    vi.spyOn(api.listings, "get").mockResolvedValue(SAMPLE_DOC);
    vi.spyOn(api.conversations, "activity").mockResolvedValue(IDLE_ACTIVITY);
    vi.spyOn(api.nativeMarketplaces, "form").mockResolvedValue(DEPOP_FORM);
    const saveSpy = vi.spyOn(api.listings, "update").mockImplementation(async (_id, listing) => mockSaveResult(listing));

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const anchor = document.createElement("button");
    const container = document.createElement("div");
    document.body.append(anchor, container);
    const root = createRoot(container);
    const onClose = vi.fn();
    const priceCol = MARKETPLACE_SHEET_COLUMNS.depop.find((c) => c.key === "price")!;

    try {
      await act(() => root.render(
        <QueryClientProvider client={client}>
          <WorkspaceRuntimeProvider value={LOCAL_RUNTIME}>
            <DesktopCellPopover
              active={{ listing: LISTING_ROW, column: priceCol, anchor }}
              marketplace="depop"
              sheetRow={READY_SHEET_ROW}
              onClose={onClose}
              onOpenListing={vi.fn()}
            />
          </WorkspaceRuntimeProvider>
        </QueryClientProvider>,
      ));
      await flushMicrotasks();

      const input = document.querySelector<HTMLInputElement>('input[data-listing-field="depop_specifics.price"]');
      expect(input).not.toBeNull();
      await act(() => {
        setInputValue(input!, "27");
      });

      // Click Done directly without blurring first — Important 3 flushes the typed value.
      const doneBtn = [...document.querySelectorAll("button")].find((btn) => btn.textContent?.trim() === "Done");
      expect(doneBtn).toBeDefined();
      await act(() => {
        doneBtn!.click();
      });
      await flushMicrotasks();

      expect(onClose).toHaveBeenCalledTimes(1);
      expect(saveSpy).toHaveBeenCalledTimes(1);
      const savedListing = saveSpy.mock.calls[0]?.[1];
      expect(savedListing).toBeDefined();
      expect((savedListing?.depop_specifics as Record<string, unknown>)?.price).toBe(27);
      expect(savedListing?.price).toBe(32);
    } finally {
      await act(() => root.unmount());
    }
  });

  it("never falls back to free-text input when native form errors or a select field is missing (B2)", async () => {
    vi.spyOn(api.listings, "get").mockResolvedValue(SAMPLE_DOC);
    vi.spyOn(api.conversations, "activity").mockResolvedValue(IDLE_ACTIVITY);
    const formSpy = vi.spyOn(api.nativeMarketplaces, "form").mockRejectedValue(new Error("Form failed"));

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const anchor = document.createElement("button");
    const container = document.createElement("div");
    document.body.append(anchor, container);
    const root = createRoot(container);
    const conditionCol = MARKETPLACE_SHEET_COLUMNS.depop.find((c) => c.key === "depop_specifics.condition")!;

    try {
      await act(() => root.render(
        <QueryClientProvider client={client}>
          <WorkspaceRuntimeProvider value={LOCAL_RUNTIME}>
            <DesktopCellPopover
              active={{ listing: LISTING_ROW, column: conditionCol, anchor }}
              marketplace="depop"
              sheetRow={READY_SHEET_ROW}
              onClose={vi.fn()}
              onOpenListing={vi.fn()}
            />
          </WorkspaceRuntimeProvider>
        </QueryClientProvider>,
      ));
      await flushMicrotasks();

      expect(document.body.textContent).toContain("Could not load Depop choices");
      expect(document.querySelector("input")).toBeNull();

      // Now return a form that omits condition — still must render read-only "Open form", never <input>.
      formSpy.mockResolvedValue({ marketplace: "depop", category_id: "tops", known: true, fields: [], notes: [] });
      client.clear();
      await act(() => root.render(
        <QueryClientProvider client={client}>
          <WorkspaceRuntimeProvider value={LOCAL_RUNTIME}>
            <DesktopCellPopover
              active={{ listing: LISTING_ROW, column: conditionCol, anchor }}
              marketplace="depop"
              sheetRow={READY_SHEET_ROW}
              onClose={vi.fn()}
              onOpenListing={vi.fn()}
            />
          </WorkspaceRuntimeProvider>
        </QueryClientProvider>,
      ));
      await flushMicrotasks();

      expect(document.querySelector("input")).toBeNull();
      expect(document.body.textContent).toContain("Open form");
    } finally {
      await act(() => root.unmount());
    }
  });

  it("disables controls and blocks saves when agent-locked or hosted-disabled (B3)", async () => {
    vi.spyOn(api.listings, "get").mockResolvedValue(SAMPLE_DOC);
    vi.spyOn(api.conversations, "activity").mockResolvedValue({
      ...IDLE_ACTIVITY,
      busy: true,
      writing: true,
      locked_fields: ["depop_specifics.price"],
    });
    vi.spyOn(api.nativeMarketplaces, "form").mockResolvedValue(DEPOP_FORM);
    const saveSpy = vi.spyOn(api.listings, "update");

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const anchor = document.createElement("button");
    const container = document.createElement("div");
    document.body.append(anchor, container);
    const root = createRoot(container);
    const priceCol = MARKETPLACE_SHEET_COLUMNS.depop.find((c) => c.key === "price")!;

    try {
      await act(() => root.render(
        <QueryClientProvider client={client}>
          <WorkspaceRuntimeProvider value={LOCAL_RUNTIME}>
            <DesktopCellPopover
              active={{ listing: LISTING_ROW, column: priceCol, anchor }}
              marketplace="depop"
              sheetRow={READY_SHEET_ROW}
              onClose={vi.fn()}
              onOpenListing={vi.fn()}
            />
          </WorkspaceRuntimeProvider>
        </QueryClientProvider>,
      ));
      await flushMicrotasks();

      const input = document.querySelector<HTMLInputElement>('input[data-listing-field="depop_specifics.price"]');
      expect(input?.disabled).toBe(true);
      expect(document.body.textContent).toContain("Updating in chat");
      expect(saveSpy).not.toHaveBeenCalled();

      // Hosted account without Depop form access also disables editing.
      vi.spyOn(api.conversations, "activity").mockResolvedValue(IDLE_ACTIVITY);
      client.clear();
      const hostedRuntime: WorkspaceRuntime = {
        ...LOCAL_RUNTIME,
        hosted: true,
        marketplaceForms: new Set(["ebay"]),
      };
      await act(() => root.render(
        <QueryClientProvider client={client}>
          <WorkspaceRuntimeProvider value={hostedRuntime}>
            <DesktopCellPopover
              active={{ listing: LISTING_ROW, column: priceCol, anchor }}
              marketplace="depop"
              sheetRow={READY_SHEET_ROW}
              onClose={vi.fn()}
              onOpenListing={vi.fn()}
            />
          </WorkspaceRuntimeProvider>
        </QueryClientProvider>,
      ));
      await flushMicrotasks();

      const hostedInput = document.querySelector<HTMLInputElement>('input[data-listing-field="depop_specifics.price"]');
      expect(hostedInput?.disabled).toBe(true);
      expect(saveSpy).not.toHaveBeenCalled();
    } finally {
      await act(() => root.unmount());
    }
  });

  it.each(["direct_category_ids.ebay", "direct_category_ids.ebay_path", null])(
    "applies the paired eBay category lock (%s)",
    async (lockedPath) => {
      vi.spyOn(api.listings, "get").mockResolvedValue(SAMPLE_DOC);
      vi.spyOn(api.conversations, "activity").mockResolvedValue({
        ...IDLE_ACTIVITY, locked_fields: lockedPath ? [lockedPath] : [],
      });
      vi.spyOn(api.nativeMarketplaces, "form").mockResolvedValue({
        ...DEPOP_FORM, marketplace: "ebay", fields: [{
          ...DEPOP_FORM.fields[1], key: "category", path: "direct_category_ids.ebay",
        }],
      });
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      client.setQueryData(["seller-region"], { country: "US", currency: "USD" });
      client.setQueryData(["catalog-categories", "ebay"], { complete: false, nodes: [] });
      const anchor = document.createElement("button");
      const container = document.createElement("div");
      document.body.append(anchor, container);
      const root = createRoot(container);
      const category = MARKETPLACE_SHEET_COLUMNS.ebay.find((column) => column.kind === "category")!;
      try {
        await act(() => root.render(
          <QueryClientProvider client={client}>
            <WorkspaceRuntimeProvider value={LOCAL_RUNTIME}>
              <DesktopCellPopover
                active={{ listing: LISTING_ROW, column: category, anchor }}
                marketplace="ebay"
                sheetRow={READY_SHEET_ROW}
                onClose={vi.fn()}
                onOpenListing={vi.fn()}
              />
            </WorkspaceRuntimeProvider>
          </QueryClientProvider>,
        ));
        await flushMicrotasks();
        const picker = document.querySelector<HTMLButtonElement>('[data-listing-field="direct_category_ids.ebay"]');
        expect(picker?.disabled).toBe(Boolean(lockedPath));
      } finally {
        await act(() => root.unmount());
        client.clear();
      }
    },
  );

  it("shows per-marketplace price in phone Quick Edit confirmation and hides List when unready", async () => {
    vi.spyOn(api.listings, "get").mockResolvedValue({
      ...SAMPLE_DOC,
      current_revision_id: "rev-fresh",
      listing: {
        title: "Wool cardigan",
        price: 32,
        depop_specifics: { price: 27, condition: "used_good" },
      },
    });
    vi.spyOn(api.conversations, "activity").mockResolvedValue(IDLE_ACTIVITY);
    vi.spyOn(api.nativeMarketplaces, "form").mockResolvedValue(DEPOP_FORM);
    const listSpy = vi.spyOn(api.nativeMarketplaces, "list").mockResolvedValue({ state: "working" } as never);

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    try {
      // Unready row must not expose a List button on phone (Important 10).
      const unreadyRow: ListingSheetRow = {
        ...READY_SHEET_ROW,
        ready: false,
        missing: [{ field: "depop_specifics.parcelSize", label: "Package size", message: "Required", rejected: false }],
      };
      await act(() => root.render(
        <QueryClientProvider client={client}>
          <WorkspaceRuntimeProvider value={LOCAL_RUNTIME}>
            <MarketplaceQuickEditSheet
              listing={LISTING_ROW}
              marketplace="depop"
              sheetRow={unreadyRow}
              nextDraftId={null}
              onClose={vi.fn()}
              onSelectListing={vi.fn()}
              onOpenListing={vi.fn()}
            />
          </WorkspaceRuntimeProvider>
        </QueryClientProvider>,
      ));
      await flushMicrotasks();
      expect([...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === "List")).toBeUndefined();

      // Ready row shows List -> confirm shows Depop's $27 (not General $32) and lists with rev-fresh.
      const priceOverrideRow: ListingSheetRow = {
        ...READY_SHEET_ROW,
        revision_id: "rev-fresh",
        cells: {
          ...READY_SHEET_ROW.cells,
          price: { value: "27", raw: "27", required: true, missing: false, rejected: false, error: "", editable: true },
        },
      };
      await act(() => root.render(
        <QueryClientProvider client={client}>
          <WorkspaceRuntimeProvider value={LOCAL_RUNTIME}>
            <MarketplaceQuickEditSheet
              listing={LISTING_ROW}
              marketplace="depop"
              sheetRow={priceOverrideRow}
              nextDraftId={null}
              onClose={vi.fn()}
              onSelectListing={vi.fn()}
              onOpenListing={vi.fn()}
            />
          </WorkspaceRuntimeProvider>
        </QueryClientProvider>,
      ));
      await flushMicrotasks();

      const listBtn = [...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === "List");
      expect(listBtn).toBeDefined();
      await act(() => {
        listBtn!.click();
      });

      expect(document.body.textContent).toContain("List on Depop for $27?");
      const renderSession = (listing: Listing | null) => root.render(
        <QueryClientProvider client={client}>
          <WorkspaceRuntimeProvider value={LOCAL_RUNTIME}>
            <MarketplaceQuickEditSheet
              listing={listing}
              marketplace="depop"
              sheetRow={priceOverrideRow}
              nextDraftId={null}
              onClose={vi.fn()}
              onSelectListing={vi.fn()}
              onOpenListing={vi.fn()}
            />
          </WorkspaceRuntimeProvider>
        </QueryClientProvider>,
      );
      await act(() => renderSession(null));
      await act(() => renderSession(LISTING_ROW));
      await flushMicrotasks();
      const findButton = (text: string) => [...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === text);
      expect(findButton("List on Depop")).toBeUndefined();
      const reopenedList = findButton("List");
      expect(reopenedList).toBeDefined();
      await act(() => reopenedList!.click());
      const confirmBtn = [...document.querySelectorAll("button")].find((b) => b.textContent?.includes("List on Depop"));
      expect(confirmBtn).toBeDefined();
      await act(() => {
        confirmBtn!.click();
      });
      await flushMicrotasks();

      expect(listSpy).toHaveBeenCalledWith("conv-1", "depop", "rev-fresh");
    } finally {
      await act(() => root.unmount());
    }
  });

  it("blocks desktop List confirmation when the ready sheet row advances with the same price", async () => {
    const listSpy = vi.spyOn(api.nativeMarketplaces, "list").mockResolvedValue({ state: "working" } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(["listing", LISTING_ROW.id], SAMPLE_DOC);
    const block = ["listing-sheet", "depop", [readinessKey(LISTING_ROW)]];
    const setRow = (revision_id: string) => client.setQueryData(block, {
      listings: { [LISTING_ROW.id]: { ...READY_SHEET_ROW, revision_id } },
    });
    setRow("rev-1");
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    try {
      await act(() => root.render(
        <QueryClientProvider client={client}>
          <WorkspaceRuntimeProvider value={LOCAL_RUNTIME}>
            <MarketplaceSheetView marketplace="depop" rows={[LISTING_ROW]} allCount={1}
              needle="" emptyLabel="No listings" loading={false} loadFailed={false}
              nameBySku={false} onOpenListing={vi.fn()} />
          </WorkspaceRuntimeProvider>
        </QueryClientProvider>,
      ));
      await flushMicrotasks();
      const button = (label: string) => [...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === label);
      const firstStep = button("List");
      expect(firstStep).toBeDefined();
      await act(() => firstStep!.click());
      expect(button("List on Depop")?.disabled).toBe(false);

      await act(() => { setRow("rev-2"); });
      await flushMicrotasks();

      const confirm = button("List on Depop");
      expect(confirm?.disabled).toBe(true);
      await act(() => confirm!.click());
      expect(listSpy).not.toHaveBeenCalled();
    } finally {
      await act(() => root.unmount());
      client.clear();
    }
  });

  it.each(["save", "discard"] as const)("gates dependent choices while the category changes (%s)", async (outcome) => {
    const initialDoc: ListingDocument = {
      ...SAMPLE_DOC,
      conversation_id: "conv-cat",
      current_revision_id: "rev-1",
      listing: {
        title: "Wool cardigan",
        price: 32,
        direct_category_ids: { depop: "cat-tops" },
      },
    };
    const catListing: Listing = { ...LISTING_ROW, id: "conv-cat" };
    const topsForm: MarketplaceForm = {
      marketplace: "depop",
      category_id: "cat-tops",
      known: true,
      fields: [
        {
          key: "category",
          path: "direct_category_ids.depop",
          label: "Category",
          required: true,
          locked: false,
          multi: false,
          selection_only: true,
          options: ["Tops", "Shoes"],
          option_values: { Tops: "cat-tops", Shoes: "cat-shoes" },
          value: "cat-tops",
        },
        {
          key: "size",
          path: "depop_specifics.size",
          label: "Size",
          required: true,
          locked: false,
          multi: false,
          selection_only: true,
          options: ["S", "M", "L"],
          value: "",
        },
      ],
      notes: [],
    };
    const shoesForm: MarketplaceForm = {
      ...topsForm,
      category_id: "cat-shoes",
      fields: [
        topsForm.fields[0],
        { ...topsForm.fields[1], options: ["US 8", "US 9", "US 10"] },
      ],
    };

    vi.spyOn(api.listings, "get").mockResolvedValue(initialDoc);
    vi.spyOn(api.conversations, "activity").mockResolvedValue(IDLE_ACTIVITY);
    const formSpy = vi.spyOn(api.nativeMarketplaces, "form").mockImplementation(async (_id, _market, rev) =>
      rev === "rev-2" ? shoesForm : topsForm,
    );

    let resolveSave!: (result: ListingSaveResult) => void;
    let rejectSave!: (error: Error) => void;
    const saveSpy = vi.spyOn(api.listings, "update").mockImplementation(
      () => new Promise<ListingSaveResult>((resolve, reject) => {
        resolveSave = resolve;
        rejectSave = reject;
      }),
    );

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    try {
      await act(() => root.render(
        <QueryClientProvider client={client}>
          <WorkspaceRuntimeProvider value={LOCAL_RUNTIME}>
            <MarketplaceQuickEditSheet
              listing={catListing}
              marketplace="depop"
              sheetRow={READY_SHEET_ROW}
              nextDraftId={null}
              onClose={vi.fn()}
              onSelectListing={vi.fn()}
              onOpenListing={vi.fn()}
            />
          </WorkspaceRuntimeProvider>
        </QueryClientProvider>,
      ));
      await flushMicrotasks();

      expect(formSpy).toHaveBeenCalledTimes(1);
      expect(formSpy).toHaveBeenLastCalledWith("conv-cat", "depop", "rev-1", expect.anything());

      // Change category to Shoes while PUT is still in flight.
      const selects = [...document.querySelectorAll<HTMLSelectElement>("select")];
      const catSelect = selects.find((el) => [...el.options].some((o) => o.value === "Shoes"));
      expect(catSelect).toBeDefined();
      await act(() => {
        catSelect!.value = "Shoes";
        catSelect!.dispatchEvent(new Event("change", { bubbles: true }));
      });
      await flushMicrotasks();

      expect(saveSpy).toHaveBeenCalledTimes(1);
      // Must NOT have queried nativeForm before the category PUT finishes.
      expect(formSpy).toHaveBeenCalledTimes(1);

      const sizeSelect = [...document.querySelectorAll("select")]
        .find((el) => [...el.options].some((option) => option.value === "S"));
      expect(sizeSelect?.disabled).toBe(true);
      expect(catSelect!.disabled).toBe(false);

      if (outcome === "discard") {
        await act(() => rejectSave(new Error("Category save failed")));
        await flushMicrotasks();
        expect(sizeSelect?.disabled).toBe(true);
        const discard = [...document.querySelectorAll("button")]
          .find((button) => button.textContent?.includes("Discard"));
        expect(discard).toBeDefined();
        await act(() => discard!.click());
        await flushMicrotasks();
        expect(sizeSelect?.disabled).toBe(false);
        expect(formSpy).toHaveBeenCalledTimes(1);
        return;
      }

      // Now finish the category save -> rev-2 -> nativeForm fetches with rev-2 and loads shoe sizes.
      await act(async () => {
        resolveSave(mockSaveResult(saveSpy.mock.calls[0][1], "rev-2"));
      });
      await flushMicrotasks();

      expect(formSpy).toHaveBeenCalledTimes(2);
      expect(formSpy).toHaveBeenLastCalledWith("conv-cat", "depop", "rev-2", expect.anything());
      expect(document.body.textContent).toContain("US 9");
    } finally {
      await act(() => root.unmount());
    }
  });

  it("blocks List when an unsaved draft remains after a failed save and invalidates confirmation on drift", async () => {
    const draftDoc: ListingDocument = {
      ...SAMPLE_DOC,
      conversation_id: "conv-guard",
      current_revision_id: "rev-1",
      listing: { title: "Wool cardigan", price: 32, depop_specifics: { price: 27, condition: "used_good" } },
    };
    const guardListing: Listing = { ...LISTING_ROW, id: "conv-guard" };
    const readyRowRev1: ListingSheetRow = {
      ...READY_SHEET_ROW,
      revision_id: "rev-1",
      cells: {
        ...READY_SHEET_ROW.cells,
        price: { value: "27", raw: "27", required: true, missing: false, rejected: false, error: "", editable: true },
      },
    };

    const getSpy = vi.spyOn(api.listings, "get").mockResolvedValue(draftDoc);
    vi.spyOn(api.conversations, "activity").mockResolvedValue(IDLE_ACTIVITY);
    vi.spyOn(api.nativeMarketplaces, "form").mockResolvedValue(DEPOP_FORM);
    vi.spyOn(api.listings, "update").mockRejectedValue(new Error("Save failed"));

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    try {
      await act(() => root.render(
        <QueryClientProvider client={client}>
          <WorkspaceRuntimeProvider value={LOCAL_RUNTIME}>
            <MarketplaceQuickEditSheet
              listing={guardListing}
              marketplace="depop"
              sheetRow={readyRowRev1}
              nextDraftId={null}
              onClose={vi.fn()}
              onSelectListing={vi.fn()}
              onOpenListing={vi.fn()}
            />
          </WorkspaceRuntimeProvider>
        </QueryClientProvider>,
      ));
      await flushMicrotasks();

      // Open List confirmation at rev-1 / $27.
      const listBtn = [...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === "List");
      expect(listBtn).toBeDefined();
      await act(() => {
        listBtn!.click();
      });
      expect(document.body.textContent).toContain("List on Depop for $27?");

      // Simulate external drift to rev-2 / $40 -> confirmation must invalidate.
      const draftDocRev2: ListingDocument = {
        ...draftDoc,
        current_revision_id: "rev-2",
        listing: { ...draftDoc.listing, depop_specifics: { price: 40, condition: "used_good" } },
      };
      getSpy.mockResolvedValue(draftDocRev2);
      const readyRowRev2: ListingSheetRow = {
        ...readyRowRev1,
        revision_id: "rev-2",
        cells: {
          ...readyRowRev1.cells,
          price: { value: "40", raw: "40", required: true, missing: false, rejected: false, error: "", editable: true },
        },
      };
      await act(() => {
        client.setQueryData<ListingDocument>(["listing", "conv-guard"], draftDocRev2);
        root.render(
          <QueryClientProvider client={client}>
            <WorkspaceRuntimeProvider value={LOCAL_RUNTIME}>
              <MarketplaceQuickEditSheet
                listing={guardListing}
                marketplace="depop"
                sheetRow={readyRowRev2}
                nextDraftId={null}
                onClose={vi.fn()}
                onSelectListing={vi.fn()}
                onOpenListing={vi.fn()}
              />
            </WorkspaceRuntimeProvider>
          </QueryClientProvider>,
        );
      });
      await flushMicrotasks();
      expect(document.body.textContent).not.toContain("List on Depop for $27?");

      // Now trigger a failed edit -> unsaved draft remains -> List button must be disabled.
      const saveSpy = vi.spyOn(api.listings, "update").mockRejectedValue(new Error("Save failed"));
      const input = document.querySelector<HTMLInputElement>('input[data-listing-field="depop_specifics.price"]');
      expect(input).not.toBeNull();
      await act(() => {
        input!.focus();
        setInputValue(input!, "19");
        input!.blur();
      });
      await flushMicrotasks();

      expect(saveSpy).toHaveBeenCalledTimes(1);
      expect(document.body.textContent).toContain("Save failed");
      const listBtnAfterFailure = [...document.querySelectorAll("button")].find(
        (b) => b.textContent?.trim() === "List",
      ) as HTMLButtonElement | undefined;
      expect(listBtnAfterFailure).toBeDefined();
      expect(listBtnAfterFailure!.disabled).toBe(true);

      // Discard changes clears the unsaved draft and re-enables List.
      const discardBtn = [...document.querySelectorAll("button")].find(
        (b) => b.textContent?.trim() === "Discard changes",
      );
      expect(discardBtn).toBeDefined();
      await act(() => {
        discardBtn!.click();
      });
      await flushMicrotasks();
      expect(document.body.textContent).not.toContain("Save failed");
      const listBtnAfterDiscard = [...document.querySelectorAll("button")].find(
        (b) => b.textContent?.trim() === "List",
      ) as HTMLButtonElement | undefined;
      expect(listBtnAfterDiscard).toBeDefined();
      expect(listBtnAfterDiscard!.disabled).toBe(false);

      // Fail again, then use Retry save once the server recovers.
      saveSpy
        .mockRejectedValueOnce(new Error("Offline"))
        .mockImplementationOnce(async (_id, listing) => mockSaveResult(listing, "rev-3"));
      const input2 = document.querySelector<HTMLInputElement>('input[data-listing-field="depop_specifics.price"]');
      expect(input2).not.toBeNull();
      await act(() => {
        input2!.focus();
        setInputValue(input2!, "21");
        input2!.blur();
      });
      await flushMicrotasks();
      expect(document.body.textContent).toContain("Offline");

      const retryBtn = [...document.querySelectorAll("button")].find(
        (b) => b.textContent?.trim() === "Retry save",
      );
      expect(retryBtn).toBeDefined();
      await act(() => {
        retryBtn!.click();
      });
      await flushMicrotasks();
      expect(saveSpy).toHaveBeenCalledTimes(3);
      expect(document.body.textContent).not.toContain("Offline");
    } finally {
      await act(() => root.unmount());
    }
  });
});
