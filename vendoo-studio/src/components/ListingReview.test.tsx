import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ListingBlockers } from "./ListingBlockers";

describe("review screens", () => {
  it("groups blockers and exposes individual fix buttons", () => {
    const html = renderToStaticMarkup(<ListingBlockers issues={[{ field: "price", message: "Price is required" }, { field: "ebay_specifics.size", message: "eBay size is required" }]} onSelect={vi.fn()} />);
    expect(html).toContain("General · 1 issue");
    expect(html).toContain("eBay · 1 issue");
    expect(html).toContain("Fix price");
  });
});
