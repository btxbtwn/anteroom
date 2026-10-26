import { describe, expect, it } from "vitest";
import { directSyncDiffers } from "./DirectSyncDetails";
import type { NativeSync, MarketplaceInventoryObservation } from "../api/nativeMarketplaces";

const inventory: MarketplaceInventoryObservation = { listing_id: "555", state: "active", price: 45.5, quantity: 3, differences: [], warning: null };
function result(values: Partial<MarketplaceInventoryObservation> = {}): NativeSync {
  return { checked_at: "2026-10-26T00:00:00Z", marketplaces: { etsy: { ok: true, error: null, inventory: { ...inventory, ...values } } } };
}
describe("direct marketplace differences", () => {
  it("flags remote price and quantity changes", () => {
    expect(directSyncDiffers(result({ differences: [{ field: "price", local: 99, remote: 45.5 }] }))).toBe(true);
    expect(directSyncDiffers(result({ differences: [{ field: "quantity", local: 4, remote: 3 }] }))).toBe(true);
  });
  it("flags inactive listings without treating them as sold", () => {
    expect(directSyncDiffers(result({ state: "ended", price: null, quantity: null }))).toBe(true);
  });
  it("does not invent a difference when no values were compared", () => {
    expect(directSyncDiffers(undefined)).toBe(false);
    expect(directSyncDiffers(result())).toBe(false);
    expect(directSyncDiffers(result({ price: null, quantity: null, warning: "Variations cannot be checked." }))).toBe(false);
  });
});

it("keeps a partial read visible even when there is no confirmed difference", async () => {
  const { directSyncIncomplete } = await import("./DirectSyncDetails");
  expect(directSyncIncomplete(result({ warning: "Variation inventory cannot be compared." }))).toBe(true);
  expect(directSyncIncomplete(result())).toBe(false);
  expect(directSyncIncomplete(undefined)).toBe(false);
});
