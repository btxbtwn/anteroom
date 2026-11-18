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
  it("offers Draft, Active and Sold, opening on Active", () => {
    const html = tabs({ draft: 1, active: 3, sold: 12 });
    const names = [...html.matchAll(/role="tab"[^>]*aria-selected="(true|false)"[^>]*title="([^"]+)"/g)].map((match) => [match[2], match[1]]);
    expect(names).toEqual([["Draft · 1", "false"], ["Active · 3", "true"], ["Sold · 12", "false"]]);
    // A space between label and count makes the accessible name "Active 3".
    expect(html).toMatch(/>Active<\/span> <span[^>]*>3</);
    expect(html).not.toContain(">Open</span>");
    expect(html).not.toContain(">Failed</span>");
  });

  it("shows no counts until the inventory has loaded", () => {
    const html = tabs(null);
    expect(html).toContain('title="Active"');
    expect(html).not.toMatch(/tabular-nums[^>]*>0</);
    expect(html).not.toContain(" · 0");
  });
});
