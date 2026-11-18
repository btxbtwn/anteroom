import { describe, expect, it } from "vitest";
import { DEFAULT_LISTING_FILTERS } from "../inventoryFilters";

const DRAFTS = { ...DEFAULT_LISTING_FILTERS, status: "draft" as const };
const SOLD = { ...DEFAULT_LISTING_FILTERS, status: "sold" as const };
import type { Listing } from "./listingShelves";
import { shelveListings } from "./shelveListings";

const open = (id: string, updated: string, extra: Partial<Listing> = {}): Listing =>
  ({ id, title: id, updated_at: updated, created_at: updated, ...extra });
const sold = (id: string, settled: string, extra: Partial<Listing> = {}): Listing =>
  ({ id, title: id, status: "sold", settled_at: settled, sold_at: settled, updated_at: settled, ...extra });

const rows = [
  open("old draft", "2026-01-01T00:00:00Z"),
  sold("sold shirt", "2026-03-01T00:00:00Z"),
  open("new draft", "2026-02-01T00:00:00Z", { sku: "SKU-9" }),
  sold("sold hat", "2026-04-01T00:00:00Z"),
];

describe("shelveListings", () => {
  it("shows drafts newest first", () => {
    const { shown, matched } = shelveListings(rows, "", DRAFTS, null);
    expect(shown.map((row) => row.id)).toEqual(["new draft", "old draft"]);
    expect(matched).toBe(2);
  });

  it("searches within the tab", () => {
    const { shown } = shelveListings(rows, "sold", SOLD, null);
    expect(shown.map((row) => row.id)).toEqual(["sold hat", "sold shirt"]);
    expect(shelveListings(rows, "hat", SOLD, null).shown.map((row) => row.id)).toEqual(["sold hat"]);
    expect(shelveListings(rows, "hat", DRAFTS, null).shown).toEqual([]);
  });

  it("matches a SKU", () => {
    expect(shelveListings(rows, "sku-9", DRAFTS, null).shown.map((row) => row.id)).toEqual(["new draft"]);
  });

  it("keeps the open listing at the top so the editor's row does not vanish", () => {
    const { shown } = shelveListings(rows, "", DRAFTS, "sold shirt");
    expect(shown.map((row) => row.id)).toEqual(["sold shirt", "new draft", "old draft"]);
  });

  it("does not add the open listing when the search rules it out", () => {
    const { shown, matched } = shelveListings(rows, "draft", DRAFTS, "sold shirt");
    expect(shown.map((row) => row.id)).toEqual(["new draft", "old draft"]);
    expect(matched).toBe(2);
  });

  it("keeps a listing that sells while open on the tab it was on, without settling", () => {
    const justSold = open("just sold", "2026-05-01T00:00:00Z", { status: "sold" });
    const { shown } = shelveListings([...rows, justSold], "", DRAFTS, "just sold");
    expect(shown.map((row) => row.id)).toEqual(["just sold", "new draft", "old draft"]);
    expect(shelveListings([...rows, justSold], "", DRAFTS, null).shown.map((row) => row.id))
      .toEqual(["new draft", "old draft"]);
  });

  it("keeps a draft that goes live while open on Draft", () => {
    const live = open("just listed", "2026-05-01T00:00:00Z", { status: "active", vendoo_marketplaces: ["ebay"] });
    expect(shelveListings([...rows, live], "", DRAFTS, "just listed").shown.map((row) => row.id))
      .toEqual(["just listed", "new draft", "old draft"]);
  });

  it("reports no match for a search nothing fits", () => {
    expect(shelveListings(rows, "zzz", DRAFTS, null)).toEqual({ shown: [], matched: 0 });
  });
});
