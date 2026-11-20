import { expect, it } from "vitest";
import { inventoryEmptyTitle } from "./inventoryEmpty";

it("says what to do next in one line, for a new and an existing inventory", () => {
  expect(inventoryEmptyTitle(0)).toBe("Create your first listing from product photos");
  expect(inventoryEmptyTitle(undefined)).toBe("Create your first listing from product photos");
  expect(inventoryEmptyTitle(4)).toBe("Open a listing, or start a new one from product photos");
});
