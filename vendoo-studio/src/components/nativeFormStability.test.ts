import { describe, expect, it } from "vitest";
import {
  categorySpecificFieldsStale,
  listingCategorySignature,
  nativeFieldVisible,
  nativeFormCategoryKey,
  nativeFormNotes,
  nativeFormStructureKey,
  presentNativeField,
  resolvedEditorValue,
  type NativeFieldContext,
  type NativeFormRow,
} from "./nativeFormStability";

const depop = {
  price: 24,
  brand: "Vassarette",
  direct_category_ids: { depop: "womenswear_tops" },
  depop_specifics: { condition: "used_good", attributes: { material: ["cotton"] } },
};

describe("native form structure", () => {
  it("stays put when a value changes and moves when the category changes", () => {
    const priced = { ...depop, price: 40, brand: "Other" };
    expect(nativeFormStructureKey(priced, "depop")).toBe(nativeFormStructureKey(depop, "depop"));
    expect(nativeFormCategoryKey(priced, "depop")).toBe("womenswear_tops");
    expect(nativeFormStructureKey({ ...depop, direct_category_ids: { depop: "womenswear_dresses" } }, "depop"))
      .toBe("womenswear_dresses");
  });

  it("reloads eBay only when the category changes, including a category written onto the listing", () => {
    const listing = {
      direct_category_ids: { ebay: "15687" },
      ebay_specifics: { listingFormat: "FIXED_PRICE", auctionStartPrice: "8", variations: { rows: [] } },
    };
    const same = { ...listing, price: 12, ebay_specifics: { ...listing.ebay_specifics, auctionStartPrice: "9" } };
    expect(nativeFormStructureKey(same, "ebay")).toBe("15687");
    expect(nativeFormStructureKey({
      ...listing,
      ebay_specifics: { ...listing.ebay_specifics, listingFormat: "AUCTION" },
    }, "ebay")).toBe(nativeFormStructureKey(listing, "ebay"));
    expect(nativeFormStructureKey({
      ...listing,
      ebay_specifics: { ...listing.ebay_specifics, variations: { rows: [{ values: { Size: "M" } }] } },
    }, "ebay")).toBe(nativeFormStructureKey(listing, "ebay"));
    expect(nativeFormStructureKey({ ebay_specifics: { categoryId: "15709" } }, "ebay")).toBe("15709");
  });

  it("reloads Mercari only when the category changes", () => {
    const listing = {
      direct_category_ids: { mercari: "1911" },
      weight_oz: 8,
      mercari_specifics: { shippingMethod: "MERCARI", brand: "Nike" },
    };
    expect(nativeFormStructureKey({ ...listing, mercari_specifics: { ...listing.mercari_specifics, brand: "Gap" } }, "mercari"))
      .toBe("1911");
    expect(nativeFormStructureKey({ ...listing, mercari_specifics: { shippingMethod: "SOYO" } }, "mercari"))
      .toBe(nativeFormStructureKey(listing, "mercari"));
    expect(nativeFormStructureKey({ ...listing, weight_oz: 16 }, "mercari"))
      .toBe(nativeFormStructureKey(listing, "mercari"));
    expect(nativeFormStructureKey({ ...listing, package_dimensions_in: "10x8x4" }, "mercari"))
      .toBe(nativeFormStructureKey(listing, "mercari"));
  });

  it("treats a category change as a new form and a price edit as the same form", () => {
    expect(listingCategorySignature({ ...depop, price: 40 })).toBe(listingCategorySignature(depop));
    expect(listingCategorySignature({ ...depop, direct_category_ids: { depop: "womenswear_dresses" } }))
      .not.toBe(listingCategorySignature(depop));
  });

  it("treats the on-screen form as the previous category only while its replacement is loading", () => {
    expect(categorySpecificFieldsStale("womenswear_tops", "womenswear_dresses", true)).toBe(true);
    expect(categorySpecificFieldsStale("womenswear_tops", "womenswear_tops", true)).toBe(false);
    expect(categorySpecificFieldsStale("womenswear_tops", "womenswear_dresses", false)).toBe(false);
  });
});

describe("native form values", () => {
  it("shows the saved value instead of the form's stale copy", () => {
    expect(resolvedEditorValue(
      { key: "price", defaultValue: 24 },
      { price: 40 },
    )).toBe(40);
    expect(resolvedEditorValue(
      { key: "depop_specifics.condition", selectionOnly: true, options: ["Good"], optionValues: { Good: "used_good" }, defaultValue: "used_fair" },
      { depop_specifics: { condition: "used_good" } },
    )).toBe("used_good");
  });

  it("shows an eBay category as its breadcrumb, not its numeric id", () => {
    expect(resolvedEditorValue(
      { key: "direct_category_ids.ebay", defaultValue: "Clothing › Women › Tops" },
      { direct_category_ids: { ebay: "15687", ebay_path: "Clothing › Women › Tops" } },
    )).toBe("Clothing › Women › Tops");
    expect(resolvedEditorValue(
      { key: "direct_category_ids.ebay", defaultValue: "Clothing › Women › Tops" },
      { direct_category_ids: { ebay: "15687" } },
    )).toBe("Clothing › Women › Tops");
  });

  it("keeps a locked computed value and a resolved choice the listing has not stored as an id", () => {
    expect(resolvedEditorValue(
      { key: "sku", locked: true, defaultValue: "" },
      { sku: "ar-abc123" },
    )).toBe("");
    expect(resolvedEditorValue(
      { key: "ebay_specifics.Condition", selectionOnly: true, options: ["Pre-owned"], optionValues: { "Pre-owned": "3000" }, defaultValue: "3000" },
      { ebay_specifics: { Condition: "Good" } },
    )).toBe("3000");
  });

  it("hides the previous category's rows, and reveals a toggle's row without reloading", () => {
    const fields = [
      { key: "price" },
      { key: "depop_specifics.attributes.material", categorySpecific: true },
      { key: "depop_specifics.worldwideShipping.enabled" },
      { key: "depop_specifics.worldwideShipping.price" },
    ];
    const context = { tab: "depop", listing: depop, local: {}, fields, categoryStale: true };
    expect(fields.filter((field) => nativeFieldVisible(field, context)).map((field) => field.key))
      .toEqual(["price", "depop_specifics.worldwideShipping.enabled"]);
    expect(nativeFieldVisible(fields[3], {
      ...context,
      categoryStale: false,
      local: { "depop_specifics.worldwideShipping.enabled": "true" },
    })).toBe(true);
    expect(nativeFieldVisible(
      { key: "weight_oz" },
      {
        tab: "mercari",
        listing: {},
        local: { "mercari_specifics.shippingMethod": "Ship on your own" },
        fields: [{ key: "mercari_specifics.shippingMethod", optionValues: { "Ship on your own": "SOYO" } }],
        categoryStale: false,
      },
    )).toBe(false);
  });

  it("shows eBay auction rows only for an auction, and offers only for a single fixed-price listing", () => {
    const fields: NativeFormRow[] = [
      { key: "ebay_specifics.listingFormat", options: ["Fixed price", "Auction"], optionValues: { "Fixed price": "FIXED_PRICE", Auction: "AUCTION" } },
      { key: "ebay_specifics.listingDuration", options: ["Good 'Til Cancelled", "7 days"], optionValues: { "Good 'Til Cancelled": "GTC", "7 days": "DAYS_7" }, defaultValue: "GTC" },
      { key: "ebay_specifics.auctionStartPrice" },
      { key: "ebay_specifics.bestOffer.enabled" },
      { key: "ebay_specifics.variations" },
      { key: "quantity" },
    ];
    const context: NativeFieldContext = {
      tab: "ebay",
      listing: { ebay_specifics: { listingFormat: "FIXED_PRICE", variations: { rows: [] } } },
      local: {},
      fields,
      categoryStale: false,
    };
    const visible = (extra: Partial<NativeFieldContext>) => fields
      .filter((field) => nativeFieldVisible(field, { ...context, ...extra }))
      .map((field) => field.key);
    expect(visible({})).toEqual([
      "ebay_specifics.listingFormat", "ebay_specifics.listingDuration", "ebay_specifics.bestOffer.enabled", "ebay_specifics.variations", "quantity",
    ]);
    expect(visible({ local: { "ebay_specifics.listingFormat": "Auction" } })).toEqual([
      "ebay_specifics.listingFormat", "ebay_specifics.listingDuration", "ebay_specifics.auctionStartPrice", "quantity",
    ]);
    const varied = {
      ...context,
      listing: { ebay_specifics: { listingFormat: "FIXED_PRICE", variations: { rows: [{ values: { Size: "M" } }] } } },
    };
    expect(visible(varied)).not.toContain("ebay_specifics.bestOffer.enabled");
    expect(visible(varied)).toContain("ebay_specifics.variations");
    const duration = presentNativeField(fields[1], { ...context, local: { "ebay_specifics.listingFormat": "Auction" } });
    expect(duration.locked).toBe(false);
    expect(duration.options).toEqual(["7 days"]);
    expect(presentNativeField(fields[1], context).locked).toBe(true);
    expect(presentNativeField(fields[0], varied).options).toEqual(["Fixed price"]);
    expect(presentNativeField(fields[5], varied).locked).toBe(true);
    expect(nativeFormNotes("ebay", varied.listing, {}, fields)).toEqual([
      "With variations, Price is the price of any variation left without its own. eBay takes no offers on a listing with variations.",
    ]);
  });

  it("locks Mercari's payer to the seller while ship on your own is selected", () => {
    const fields: NativeFormRow[] = [
      { key: "mercari_specifics.shippingMethod", options: ["Mercari shipping", "Ship on your own"], optionValues: { "Mercari shipping": "MERCARI", "Ship on your own": "SOYO" } },
      { key: "mercari_specifics.shippingPayerId", options: ["Buyer", "Seller"], optionValues: { Buyer: "1", Seller: "2" }, defaultValue: "1" },
    ];
    const payer = presentNativeField(fields[1], {
      tab: "mercari",
      listing: {},
      local: { "mercari_specifics.shippingMethod": "Ship on your own" },
      fields,
      categoryStale: false,
    });
    expect(payer).toMatchObject({ locked: true, options: ["Seller"], defaultValue: "2" });
    expect(nativeFormNotes("mercari", {}, { "mercari_specifics.shippingMethod": "Ship on your own" }, fields)).toEqual([
      "Buy and provide your own shipping label; the seller pays.",
    ]);
  });
});
