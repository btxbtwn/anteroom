import { describe, expect, it } from "vitest";
import type { StillListedItem } from "../api/directConnections";
import { stillListedLine } from "./StillListedAlert";

const item = (stillListed: StillListedItem["stillListed"]): StillListedItem => ({
  conversationId: "coat", title: "Coat", saleId: "sale", soldOn: "poshmark", soldAt: null, detected: true, stillListed,
});
const entry = (marketplace: string, state: "live" | "failed") => ({ marketplace, state, error: null, at: null, attempts: 0, url: null });

describe("still listed after a sale", () => {
  it("names where it sold and every marketplace it is still on", () => {
    expect(stillListedLine(item([entry("ebay", "live"), entry("mercari", "live")]))).toBe("Sold on Poshmark, still on eBay and Mercari");
  });
  it("counts the delists that failed", () => {
    expect(stillListedLine(item([entry("ebay", "failed")]))).toBe("Sold on Poshmark, still on eBay (delist failed)");
    expect(stillListedLine(item([entry("ebay", "failed"), entry("mercari", "failed")]))).toContain("2 delists failed");
  });
});
