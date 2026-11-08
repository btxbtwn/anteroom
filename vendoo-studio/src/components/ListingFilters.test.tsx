import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ListingFilters } from "./ListingFilters";
import { DEFAULT_LISTING_FILTERS } from "./inventoryFilters";

function tabs(counts: Record<string, number> | null) {
  return renderToStaticMarkup(
    <ListingFilters filters={DEFAULT_LISTING_FILTERS} counts={counts} labels={[]} labelCounts={{}} marketplaces={[]}
      marketplaceCounts={{}} notListedCount={0} unsentCount={0} missingSaleCount={0} staleCounts={{}} onChange={() => {}} />,
  );
}

describe("ListingFilters status tabs", () => {
  it("names the working list Open, since sold listings have their own tab", () => {
    const html = tabs({ all: 4, draft: 1, active: 3, sold: 12 });
    expect(html).toContain('title="Open · 4"');
    expect(html).toContain('aria-label="Open, 4"');
    expect(html).toContain(">Open</span>");
    expect(html).not.toContain(">All</span>");
    expect(html).toContain('title="Sold · 12"');
  });

  it("shows no counts until the inventory has loaded", () => {
    const html = tabs(null);
    expect(html).toContain('title="Open"');
    expect(html).not.toMatch(/tabular-nums[^>]*>0</);
    expect(html).not.toContain(" · 0");
  });
});
