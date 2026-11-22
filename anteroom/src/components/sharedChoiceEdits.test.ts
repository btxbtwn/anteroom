import { expect, it } from "vitest";
import { editorFieldValue } from "./ListingEditor";
import { resolvedEditorValue } from "./nativeFormStability";
import { setListingEditorValue } from "../listingPaths";
import type { ListingData } from "../api/types";

it.each(["ebay", "poshmark", "mercari", "depop", "etsy"])(
  "%s keeps cleared and replaced choices through a stale form refresh", (marketplace) => {
    for (const nested of [false, true]) {
      for (const multi of [false, true]) {
        const key = `${marketplace}_specifics.${nested ? "attributes." : ""}choice`;
        const field = { key, label: "Choice", native: true, selectionOnly: true, multi,
          defaultValue: multi ? ["old"] : "old", options: ["Old", "New"],
          optionValues: { Old: "old", New: "new" } };
        const listing: ListingData = {};
        setListingEditorValue(listing, key, multi ? ["old"] : "old");
        for (const next of [multi ? [] : "", multi ? ["new"] : "new"]) {
          setListingEditorValue(listing, key, next);
          expect(editorFieldValue(listing, field, marketplace)).toEqual(next);
          expect(resolvedEditorValue(field, listing)).toEqual(next);
        }
      }
    }
  },
);

it.each(["age", "source", "style"])("keeps Depop %s empty instead of restoring the old chips", (name) => {
  const key = `depop_specifics.${name}`;
  const listing: ListingData = { depop_specifics: { [name]: [] } };
  expect(editorFieldValue(listing, { key, label: name, native: true, multi: true,
    selectionOnly: true, defaultValue: ["Old"], options: ["Old", "New"] }, "depop")).toEqual([]);
});

it("shows the rule-derived marketplace price without giving up explicit prices or the rules-off fallback", () => {
  const field = { key: "ebay_specifics.price", label: "eBay price", type: "number" as const, native: true, defaultValue: 24 };
  const listing = { price: 20, ebay_specifics: { price: 22 }, _price_followers: { "ebay_specifics.price": 22 } };
  expect(editorFieldValue(listing, field, "ebay", true)).toBe(24);
  expect(editorFieldValue(listing, field, "ebay", false)).toBe(20);
  expect(editorFieldValue({ ...listing, seller_fields: [field.key] }, field, "ebay", true)).toBe(22);
  expect(editorFieldValue(listing, { ...field, native: false, defaultValue: undefined }, "ebay", true)).toBe(22);
});
