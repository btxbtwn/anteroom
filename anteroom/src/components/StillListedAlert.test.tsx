import { describe, expect, it } from "vitest";
import type { StillListedItem } from "../api/directConnections";
import { stillListedFooter, stillListedLine } from "./StillListedAlert";

const item = (stillListed: StillListedItem["stillListed"]): StillListedItem => ({
  conversationId: "coat", title: "Coat", saleId: "sale", soldOn: "poshmark", soldAt: null, detected: true, pinned: true, stillListed,
});
const entry = (marketplace: string, state: "live" | "failed", followed = true) => ({ marketplace, listingId: `${marketplace}-1`, followed, state, error: null, at: null, attempts: 0, url: null });

describe("still listed after a sale", () => {
  it("names where it sold and every marketplace it is still on", () => {
    expect(stillListedLine(item([entry("ebay", "live"), entry("mercari", "live")]))).toBe("Sold on Poshmark, still on eBay and Mercari");
  });
  it("counts the delists that failed", () => {
    expect(stillListedLine(item([entry("ebay", "failed")]))).toBe("Sold on Poshmark, still on eBay (delist failed)");
    expect(stillListedLine(item([entry("ebay", "failed"), entry("mercari", "failed")]))).toContain("2 delists failed");
  });
  it("promises retries only for listings the sale left up, and only when the seller turned them on", () => {
    expect(stillListedFooter([item([entry("ebay", "failed")])], true)).toBe("Anteroom keeps trying to delist them. Open an item to see why a delist failed.");
    expect(stillListedFooter([item([entry("ebay", "failed")])], false)).toBe("Open each item and delist it under Marketplaces.");
    expect(stillListedFooter([item([entry("ebay", "live", false)])], true)).toContain("put up after the sale is yours to delist");
    expect(stillListedFooter([{ ...item([entry("ebay", "live")]), detected: false }], true)).toContain("marked by hand");
  });
});
