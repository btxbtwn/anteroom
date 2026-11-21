import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { WorkspaceRuntimeProvider } from "../app/runtime";
import { InventoryTablePage } from "./InventoryTablePage";
import type { Listing } from "./sidebar/listingShelves";

function table(conversations: Listing[] | undefined, loadFailed = false, initialTab?: "overview" | "ebay" | "poshmark" | "mercari" | "depop" | "etsy") {
  return renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <WorkspaceRuntimeProvider value={{ hosted: false, features: new Set(), views: new Set(["listings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
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
});
