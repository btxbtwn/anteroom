import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AutomationsPage } from "./AutomationsPage";
import { WorkspaceRuntimeProvider } from "../app/runtime";

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) => ({
    data: queryKey[0] === "ebay-offers"
      ? { connected: true, listings: [], history: [], lastCheck: null, lastError: null,
        allListings: { enabled: false, discountPercent: 10, minimumPrice: "" } }
      : [],
    isLoading: false,
    error: null,
  }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

describe("AutomationsPage", () => {
  it("asks for a sentence and does not run anything until it is confirmed", () => {
    const html = renderToStaticMarkup(<AutomationsPage onOpenListing={vi.fn()} />);
    expect(html).toContain("Automations");
    expect(html).toContain("Listing, delisting and relisting wait for your review");
    expect(html).not.toContain("Require approval before publishing");
    expect(html).toContain("Draft from prompt");
    expect(html).toContain("Preview");
    expect(html).toContain("Save and confirm");
    expect(html).toContain("60 days listed on eBay and Poshmark");
    expect(html).toContain("No automations yet.");
    expect(html).toContain("Waiting for approval");
    expect(html).toContain("Nothing is waiting for approval.");
    expect(html).toContain('aria-label="eBay offers"');
    expect(html).toContain("Automatic offers check every 15 minutes");
    expect(html).toContain("No unsold listings with eBay links");
  });

  it("on the web, shows eBay offers only while the offer timer runs", () => {
    const page = (features: string[]) => renderToStaticMarkup(
      <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(features as never[]), views: new Set(["automations"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
        <AutomationsPage onOpenListing={vi.fn()} />
      </WorkspaceRuntimeProvider>,
    );
    const html = page(["inventory"]);
    expect(html).toContain("Draft from prompt");
    expect(html).toContain("Waiting for approval");
    expect(html).not.toContain('aria-label="eBay offers"');
    expect(page(["inventory", "ebay_offers"])).toContain('aria-label="eBay offers"');
  });
});
