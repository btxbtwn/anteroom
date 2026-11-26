// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ComponentProps } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkspaceRuntimeProvider } from "../app/runtime";
import { InventoryTablePage } from "./InventoryTablePage";
import type { Listing } from "./sidebar/listingShelves";

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: (options: { count: number; getItemKey?: (index: number) => string | number; estimateSize: () => number }) => {
    const size = options.estimateSize();
    return {
      getVirtualItems: () =>
        Array.from({ length: options.count }, (_, index) => ({
          key: options.getItemKey ? options.getItemKey(index) : index,
          index,
          start: index * size,
          size,
        })),
      getTotalSize: () => options.count * size,
      measure: () => {},
      measureElement: () => {},
    };
  },
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const RUNTIME = {
  hosted: false,
  features: new Set<string>(),
  views: new Set(["listings"]),
  marketplaceForms: new Set<string>(),
  actionMarketplaces: new Set<string>(),
};

function table(
  conversations: Listing[] | undefined,
  loadFailed = false,
  initialTab?: "overview" | "ebay" | "poshmark" | "mercari" | "depop" | "etsy",
) {
  return renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <WorkspaceRuntimeProvider value={RUNTIME}>
        <InventoryTablePage conversations={conversations} loadFailed={loadFailed} initialTab={initialTab} onOpenListing={() => {}} />
      </WorkspaceRuntimeProvider>
    </QueryClientProvider>,
  );
}

const sampleListing: Listing = {
  id: "conv-1",
  title: "Vintage Denim Jacket",
  price: 42,
  status: "active",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

const driftedListing: Listing = {
  ...sampleListing,
  id: "conv-drift",
  title: "Patagonia Fleece Pullover",
  price: 60,
  price_drift: [
    { marketplace: "poshmark", price: 60, expected: 68 },
    { marketplace: "depop", price: 55, expected: 62 },
  ],
};

function mockPhoneMedia(matches: boolean) {
  const original = window.matchMedia;
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: query.includes("max-width: 639.98px") ? matches : false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
  return () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: original,
    });
  };
}

async function mountPage(props: ComponentProps<typeof InventoryTablePage>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(["settings-pricing-rules"], { enabled: false });
  client.setQueryData(["settings-listing-warnings"], { missing_sku: false });
  client.setQueryData(["settings-marketplaces"], { selected: ["ebay", "poshmark", "mercari", "depop", "etsy"] });
  client.setQueryData(["settings-ui"], { listing_names: "title", label_colors: {} });
  client.setQueryData(["listing-readiness", ""], { listings: {} });
  if (props.conversations?.length) {
    client.setQueryData(
      ["listing-readiness", props.conversations.map((item) => item.id).join(",")],
      { listings: {} },
    );
  }
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <WorkspaceRuntimeProvider value={RUNTIME}>
          <InventoryTablePage {...props} />
        </WorkspaceRuntimeProvider>
      </QueryClientProvider>,
    );
  });
  return {
    container,
    unmount: async () => {
      await act(async () => {
        root.unmount();
      });
      container.remove();
      client.clear();
    },
  };
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("InventoryTablePage", () => {
  it("shows placeholder rows, not an empty inventory, while loading", () => {
    const html = table(undefined);
    expect(html).toContain('aria-label="Loading listings"');
    expect(html).not.toContain("No listings yet");
    expect(html).toContain(">Active<");
    expect(html).not.toContain(">Open<");
    expect(html).not.toMatch(/tabular-nums">0</);
  });

  it("says the inventory could not load instead of loading forever", () => {
    const html = table(undefined, true);
    expect(html).toContain("Could not load listings");
    expect(html).not.toContain('aria-label="Loading listings"');
  });

  it("says the inventory is empty once it has loaded empty", () => {
    const html = table([]);
    expect(html).toContain("No listings yet");
    expect(html).not.toContain('aria-label="Loading listings"');
  });

  it("renders Overview and marketplace sheet tabs", () => {
    const html = table([sampleListing]);
    expect(html).toContain('aria-label="Inventory views"');
    expect(html).toContain(">Overview<");
    expect(html).toContain(">eBay<");
    expect(html).toContain(">Poshmark<");
    expect(html).toContain(">Mercari<");
    expect(html).toContain(">Depop<");
    expect(html).toContain(">Etsy<");
  });

  it("renders the marketplace spreadsheet view when a marketplace tab is active", () => {
    const html = table([sampleListing], false, "depop");
    expect(html).toContain('aria-label="Depop inventory spreadsheet"');
    expect(html).toContain(">Category<");
    expect(html).toContain(">Package size<");
    expect(html).toContain("Vintage Denim Jacket");
  });

  it.each([
    { tab: "overview" as const, phone: false },
    { tab: "depop" as const, phone: false },
    { tab: "depop" as const, phone: true },
  ])(
    "renders the shared error alert with failure reason and Retry button on $tab (phone=$phone)",
    async ({ tab, phone }) => {
      const restoreMedia = mockPhoneMedia(phone);
      const onRetry = vi.fn();
      const view = await mountPage({
        conversations: undefined,
        loadFailed: true,
        loadError: "Database is temporarily unavailable",
        onRetry,
        initialTab: tab,
        onOpenListing: () => {},
      });
      try {
        const alert = view.container.querySelector('[role="alert"]');
        expect(alert).not.toBeNull();
        expect(alert?.textContent).toContain("Could not load listings: Database is temporarily unavailable");
        expect(view.container.textContent).not.toContain("Loading…");
        const retryButton = [...view.container.querySelectorAll("button")].find(
          (button) => button.textContent?.trim() === "Retry",
        );
        expect(retryButton).toBeDefined();
        expect(retryButton?.disabled).toBe(false);
        await act(async () => {
          retryButton?.click();
        });
        expect(onRetry).toHaveBeenCalledTimes(1);
      } finally {
        await view.unmount();
        restoreMedia();
      }
    },
  );

  it("disables Retry and shows Retrying… while a manual retry is in flight", async () => {
    const view = await mountPage({
      conversations: undefined,
      loadFailed: true,
      loadError: "Connection reset",
      retrying: true,
      onRetry: () => {},
      onOpenListing: () => {},
    });
    try {
      const retryButton = [...view.container.querySelectorAll("button")].find(
        (button) => button.textContent?.trim() === "Retrying…",
      );
      expect(retryButton).toBeDefined();
      expect(retryButton?.disabled).toBe(true);
    } finally {
      await view.unmount();
    }
  });

  it("renders a stacked list instead of a fixed-width table on phones", async () => {
    const restoreMedia = mockPhoneMedia(true);
    const view = await mountPage({
      conversations: [sampleListing],
      onOpenListing: () => {},
    });
    try {
      expect(view.container.querySelector('[role="table"][aria-label="Inventory"]')).toBeNull();
      const list = view.container.querySelector('[role="list"][aria-label="Inventory"]');
      expect(list).not.toBeNull();
      expect(list?.querySelectorAll('[role="listitem"]').length).toBe(1);
      expect(list?.textContent).toContain("Vintage Denim Jacket");
      expect(list?.textContent).toContain("$42");
      expect(list?.querySelector('button[aria-label*="on eBay:"]')).not.toBeNull();
    } finally {
      await view.unmount();
      restoreMedia();
    }
  });

  it.each([false, true])(
    "opens a tap-accessible popover with price drift details without opening the listing (phone=%s)",
    async (phone) => {
      const restoreMedia = mockPhoneMedia(phone);
      const onOpenListing = vi.fn();
      const view = await mountPage({
        conversations: [driftedListing],
        onOpenListing,
      });
      try {
        const trigger = [...view.container.querySelectorAll("button")].find(
          (button) => button.textContent?.includes("Price inconsistency"),
        );
        expect(trigger).toBeDefined();
        expect(trigger?.closest("button")).toBe(trigger);
        expect(trigger?.getAttribute("title")).toBeNull();

        await act(async () => {
          trigger?.click();
        });
        expect(onOpenListing).not.toHaveBeenCalled();
        const popup = document.querySelector('[data-slot="popover-popup"]');
        expect(popup).not.toBeNull();
        expect(popup?.textContent).toContain("Poshmark");
        expect(popup?.textContent).toContain("$60");
        expect(popup?.textContent).toContain("$68");
        expect(popup?.textContent).toContain("Depop");
        expect(popup?.textContent).toContain("$62");
      } finally {
        await view.unmount();
        restoreMedia();
      }
    },
  );
});
