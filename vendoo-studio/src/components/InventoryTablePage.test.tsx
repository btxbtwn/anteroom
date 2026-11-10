import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { WorkspaceRuntimeProvider } from "../app/runtime";
import { InventoryTablePage } from "./InventoryTablePage";
import type { Listing } from "./sidebar/listingShelves";

function table(conversations: Listing[] | undefined, loadFailed = false) {
  return renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <WorkspaceRuntimeProvider value={{ hosted: false, features: new Set(), views: new Set(["listings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
        <InventoryTablePage conversations={conversations} loadFailed={loadFailed} onOpenListing={() => {}} />
      </WorkspaceRuntimeProvider>
    </QueryClientProvider>,
  );
}

describe("InventoryTablePage", () => {
  it("shows placeholder rows, not an empty inventory, while loading", () => {
    const html = table(undefined);
    expect(html).toContain('aria-label="Loading listings"');
    expect(html).not.toContain("No listings yet");
    expect(html).toContain(">Open<");
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
});
