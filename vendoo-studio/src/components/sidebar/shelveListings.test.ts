import { describe, expect, it } from "vitest";
import { DEFAULT_LISTING_FILTERS } from "../inventoryFilters";
import type { Listing } from "./listingShelves";
import { shelveListings } from "./shelveListings";

const open = (id: string, updated: string, extra: Partial<Listing> = {}): Listing =>
  ({ id, title: id, updated_at: updated, created_at: updated, ...extra });
const sold = (id: string, settled: string, extra: Partial<Listing> = {}): Listing =>
  ({ id, title: id, settled_at: settled, sold_at: settled, updated_at: settled, ...extra });

const rows = [
  open("old draft", "2026-01-01T00:00:00Z"),
  sold("sold shirt", "2026-03-01T00:00:00Z"),
  open("new draft", "2026-02-01T00:00:00Z", { sku: "SKU-9" }),
  sold("sold hat", "2026-04-01T00:00:00Z"),
];

describe("shelveListings", () => {
  it("shows open listings newest first and keeps finished ones off the working list", () => {
    const { shown, matched } = shelveListings(rows, "", DEFAULT_LISTING_FILTERS, null);
    expect(shown.map((row) => row.id)).toEqual(["new draft", "old draft"]);
    expect(matched).toBe(4);
  });

  it("finds finished listings while searching, below the open ones", () => {
    const { shown } = shelveListings(rows, "sold", DEFAULT_LISTING_FILTERS, null);
    expect(shown.map((row) => row.id)).toEqual(["sold hat", "sold shirt"]);
    const hats = shelveListings(rows, "hat", DEFAULT_LISTING_FILTERS, null);
    expect(hats.shown.map((row) => row.id)).toEqual(["sold hat"]);
  });

  it("matches a SKU", () => {
    expect(shelveListings(rows, "sku-9", DEFAULT_LISTING_FILTERS, null).shown.map((row) => row.id)).toEqual(["new draft"]);
  });

  it("keeps the open finished listing at the top so the editor's row does not vanish", () => {
    const { shown } = shelveListings(rows, "", DEFAULT_LISTING_FILTERS, "sold shirt");
    expect(shown.map((row) => row.id)).toEqual(["sold shirt", "new draft", "old draft"]);
  });

  it("does not add the open listing when the search rules it out", () => {
    const { shown, matched } = shelveListings(rows, "draft", DEFAULT_LISTING_FILTERS, "sold shirt");
    expect(shown.map((row) => row.id)).toEqual(["new draft", "old draft"]);
    expect(matched).toBe(2);
  });

  it("reports no match for a search nothing fits", () => {
    expect(shelveListings(rows, "zzz", DEFAULT_LISTING_FILTERS, null)).toEqual({ shown: [], matched: 0 });
  });
});
