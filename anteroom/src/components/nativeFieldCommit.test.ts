import { describe, expect, it } from "vitest";
import type { ListingData } from "../api/types";
import {
  coerceNumericValue,
  commitNativeField,
  independentFieldKey,
  isInheritedGeneralLock,
  optionLabelForValue,
  type CommitFieldSpec,
} from "./nativeFieldCommit";

describe("nativeFieldCommit", () => {
  it("maps labels through optionValues for single and multi-select fields", () => {
    const fields: CommitFieldSpec[] = [
      { key: "depop_specifics.condition", optionValues: { "Used - Good": "used_good" } },
      { key: "depop_specifics.colour", optionValues: { Black: "black", Blue: "blue" } },
    ];
    const base: ListingData = { title: "Vintage Tee" };

    const afterSingle = commitNativeField(base, fields, "depop_specifics.condition", "Used - Good");
    expect((afterSingle?.depop_specifics as Record<string, unknown>)?.condition).toBe("used_good");
    // Does not mutate input
    expect(base.depop_specifics).toBeUndefined();

    const afterMulti = commitNativeField(afterSingle!, fields, "depop_specifics.colour", ["Black", "Blue"]);
    expect((afterMulti?.depop_specifics as Record<string, unknown>)?.colour).toEqual(["black", "blue"]);
  });

  it("coerces numeric values and clears empty numbers to null", () => {
    const fields: CommitFieldSpec[] = [{ key: "price", type: "number" }];
    expect(coerceNumericValue("24.5")).toBe(24.5);
    expect(coerceNumericValue("")).toBeNull();
    expect(coerceNumericValue("abc")).toBe("abc");

    const priced = commitNativeField({}, fields, "price", "28");
    expect(priced?.price).toBe(28);
    const cleared = commitNativeField(priced!, fields, "price", "");
    expect(cleared?.price).toBeNull();
  });

  it("rewrites marketplace price and followed-general keys so Depop price edits leave listing.price unchanged", () => {
    expect(independentFieldKey("depop", "price")).toBe("depop_specifics.price");
    expect(independentFieldKey("poshmark", "price")).toBe("poshmark_specifics.poshmarkPrice");
    expect(independentFieldKey("ebay", "price")).toBe("ebay_specifics.price");
    expect(independentFieldKey("depop", "title")).toBe("depop_specifics.title");
    expect(independentFieldKey("general", "price")).toBe("price");
    expect(isInheritedGeneralLock(true, "Set on General. Change it there.")).toBe(true);
    expect(isInheritedGeneralLock(true, "Computed by Poshmark.")).toBe(false);

    const depopPriceKey = independentFieldKey("depop", "price");
    const fields: CommitFieldSpec[] = [{ key: depopPriceKey, type: "number" }];
    const base: ListingData = { price: 32, title: "Wool cardigan" };
    const updated = commitNativeField(base, fields, depopPriceKey, "27");
    expect((updated?.depop_specifics as Record<string, unknown>)?.price).toBe(27);
    expect(updated?.price).toBe(32);
  });

  it("applies companion paths (`also`), typed dependent clears, and Poshmark sizeSetId sync", () => {
    const fields: CommitFieldSpec[] = [
      {
        key: "direct_category_ids.depop",
        optionValues: { "Menswear > Tops > T-shirts": "menswear_tshirts" },
        clears: ["depop_specifics.size", "depop_specifics.attributes", "poshmark_specifics.inventoryRows", "mercari_specifics.noBrand"],
      },
      { key: "mercari_specifics.noBrand", type: "boolean" },
      { key: "direct_category_ids.etsy", optionValues: { "Clothing > Shirts": "42" } },
      { key: "poshmark_specifics.nativeSizeId" },
      { key: "poshmark_specifics.sizeSetId", defaultValue: "standard-set" },
    ];
    const base: ListingData = {
      depop_specifics: { size: "L", attributes: { sleeve: ["Short"] } },
      poshmark_specifics: { inventoryRows: [{ sizeSetId: "a", sizeId: "M", quantity: 1 }] },
      mercari_specifics: { noBrand: true },
    };

    const depop = commitNativeField(base, fields, "direct_category_ids.depop", "Menswear > Tops > T-shirts");
    expect((depop?.direct_category_ids as Record<string, unknown>)?.depop).toBe("menswear_tshirts");
    expect((depop?.depop_specifics as Record<string, unknown>)?.size).toBe("");
    expect((depop?.depop_specifics as Record<string, unknown>)?.attributes).toEqual({});
    expect((depop?.poshmark_specifics as Record<string, unknown>)?.inventoryRows).toEqual([]);
    expect((depop?.mercari_specifics as Record<string, unknown>)?.noBrand).toBe(false);

    const etsy = commitNativeField(base, fields, "direct_category_ids.etsy", "Clothing > Shirts", {
      also: { "direct_category_ids.etsy_path": "Clothing > Shirts" },
    });
    expect((etsy?.direct_category_ids as Record<string, unknown>)?.etsy).toBe("42");
    expect((etsy?.direct_category_ids as Record<string, unknown>)?.etsy_path).toBe("Clothing > Shirts");

    const posh = commitNativeField(base, fields, "poshmark_specifics.nativeSizeId", "M");
    expect((posh?.poshmark_specifics as Record<string, unknown>)?.sizeSetId).toBe("standard-set");
  });

  it("blocks edits when disabled, field-locked, agent-locked, or [object Object]", () => {
    const fields: CommitFieldSpec[] = [
      { key: "price", type: "number" },
      { key: "poshmark_specifics.earnings", locked: true },
    ];
    expect(commitNativeField({}, fields, "price", "10", { disabled: true })).toBeNull();
    expect(commitNativeField({}, fields, "poshmark_specifics.earnings", "$8")).toBeNull();
    expect(commitNativeField({}, fields, "price", "10", { agentLocks: ["price"] })).toBeNull();
    expect(commitNativeField({}, fields, "price", "[object Object]")).toBeNull();
  });

  it("reverse-maps stored option IDs to display labels", () => {
    expect(optionLabelForValue("used_good", { "Used - Good": "used_good" })).toBe("Used - Good");
    expect(optionLabelForValue("unknown", { "Used - Good": "used_good" })).toBe("unknown");
  });
});
