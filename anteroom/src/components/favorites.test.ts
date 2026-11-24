import { describe, expect, it } from "vitest";
import { queueFavoriteWrite, withFavorite } from "./favorites";
import { DEFAULT_LISTING_FILTERS, activeFilterCount, favoriteCount, filterListings } from "./inventoryFilters";
import { inventoryRows } from "./inventoryTable";
import { tableFilterCount, NO_TABLE_FILTERS } from "./InventoryTableFilters";

const rows = [
  { id: "jeans", title: "Levi's jeans", status: "draft", favorite: true },
  { id: "coat", title: "Wool coat", status: "draft", favorite: false },
  { id: "bag", title: "Coach bag", status: "draft" },
];
const draft = { ...DEFAULT_LISTING_FILTERS, status: "draft" as const };

describe("favorites", () => {
  it("keeps only starred listings under the Favorites filter and counts it as one filter", () => {
    expect(filterListings(rows, "", draft).map((row) => row.id)).toEqual(["jeans", "coat", "bag"]);
    const favorites = { ...draft, favorites: true };
    expect(filterListings(rows, "", favorites).map((row) => row.id)).toEqual(["jeans"]);
    expect(activeFilterCount(draft)).toBe(0);
    expect(activeFilterCount(favorites)).toBe(1);
    expect(favoriteCount(rows)).toBe(1);
  });

  it("filters the inventory table the same way", () => {
    const table = { ...NO_TABLE_FILTERS, favorites: true };
    expect(tableFilterCount(table)).toBe(1);
    expect(inventoryRows(rows, "", "draft", "recent", table).map((row) => row.id)).toEqual(["jeans"]);
  });

  it("changes only the starred row in the cached inventory", () => {
    const next = withFavorite(rows, { id: "coat", favorite: true });
    expect(next?.map((row) => row.favorite)).toEqual([true, true, undefined]);
    expect(next?.[0]).toBe(rows[0]);
    expect(withFavorite(undefined, { id: "coat", favorite: true })).toBeUndefined();
  });

  it("sends an item's star writes in order so the last choice is saved", async () => {
    const saved: boolean[] = [];
    let finishStar!: () => void;
    const star = queueFavoriteWrite("coat", () => new Promise<void>((resolve) => {
      finishStar = () => { saved.push(true); resolve(); };
    }));
    const unstar = queueFavoriteWrite("coat", async () => { saved.push(false); });
    const other = queueFavoriteWrite("jeans", async () => { saved.push(true); });
    await expect(other).resolves.toBe(true);
    expect(saved).toEqual([true]);
    finishStar();
    await expect(star).resolves.toBe(false);
    await expect(unstar).resolves.toBe(true);
    expect(saved).toEqual([true, true, false]);
  });

  it("reports only the latest write's failure", async () => {
    const older = queueFavoriteWrite("bag", () => Promise.reject(new Error("offline")));
    const newer = queueFavoriteWrite("bag", () => Promise.reject(new Error("still offline")));
    await expect(older).resolves.toBe(false);
    await expect(newer).rejects.toThrow("still offline");
    await expect(queueFavoriteWrite("bag", async () => {})).resolves.toBe(true);
  });
});
