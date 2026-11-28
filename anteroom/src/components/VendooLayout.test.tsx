import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { vendooRail } from "./ListingEditor";
import { VendooLayout } from "./VendooLayout";

vi.mock("@tanstack/react-query", async (original) => ({
  ...await original<typeof import("@tanstack/react-query")>(),
  useQuery: () => ({ data: undefined, isPending: false, error: null }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn(), getQueryData: vi.fn() }),
}));

vi.mock("./chat/liveChatStore", async (original) => ({
  ...await original<typeof import("./chat/liveChatStore")>(),
  useChatBusy: () => false,
}));

function render(rail: ReturnType<typeof vendooRail>) {
  return renderToStaticMarkup(
    <VendooLayout convId="item" revisionId="current" title="Tee" disabled={false} etsyEligible
      ids={["general", "ebay", "poshmark"]} selected="general" onSelect={() => {}} {...rail}>
      {null}
    </VendooLayout>,
  );
}

// The seller asked for the Vendoo listing mode to stay as it is. Its rail keeps
// Regenerate, the favorite and "More listing actions", and none of the direct
// editor's toolbar, Tracking menu or Mark as sold changes reach it.
describe("Vendoo listing mode rail", () => {
  const rail = (overrides: Partial<Parameters<typeof vendooRail>[0]> = {}) => vendooRail({
    convId: "item", conversation: { favorite: false }, title: "Tee", generation: true, saving: false, generating: false, ...overrides,
  });

  it("keeps its forms, Regenerate, favorite and overflow menu in the rail", () => {
    const html = render(rail());
    const nav = html.slice(html.indexOf('aria-label="Listing forms"'), html.indexOf("</nav>"));
    const order = ["General", "eBay", "Poshmark", "Regenerate…", 'aria-label="More listing actions"'];
    for (const label of order) expect(nav).toContain(label);
    // The overflow menu (with the favorite) sits above Regenerate, pinned to the rail's foot as before.
    expect(nav.indexOf('aria-label="More listing actions"')).toBeLessThan(nav.indexOf("Regenerate…"));
    expect(nav).toMatch(/<span class="absolute end-0\.5 top-2 z-10"><span class="flex items-center gap-0\.5">/);
    // Regenerate keeps its quiet rail style.
    expect(nav.match(/<button[^>]*>(?:(?!<\/button>).)*Regenerate…<\/button>/)?.[0]).toContain("text-muted-foreground");
  });

  it("gets none of the direct editor's toolbar or Tracking changes", () => {
    const html = render(rail());
    for (const text of ['aria-label="Listing actions"', "List everywhere", "Schedule…", "Tracking", "Mark as sold", "Delist everywhere"]) {
      expect(html).not.toContain(text);
    }
  });

  it("leaves Regenerate out without generation and says what it says while saving", () => {
    expect(render(rail({ generation: false }))).not.toContain("Regenerate…");
    expect(render(rail({ saving: true, generating: true }))).toContain("Edits kept. Waiting for chat to finish before saving…");
  });
});
