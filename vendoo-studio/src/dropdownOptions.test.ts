import { describe, expect, it } from "vitest";
import { optionsForField, withDropdownOptions, withSchemaOptions } from "./dropdownOptions";

type Field = { key: string; label: string; options?: string[] };

const FORMS = {
  vendoo: {
    condition: ["New With Tags/Box", "Pre-Owned - Good"],
    usSize: ["S", "M", "L"],
  },
  depop: {
    style: ["Streetwear", "Sportswear"],
    productType: ["jackets", "tshirts"],
  },
  mercari: {
    shippingLabel: ["USPS Ground Advantage / 1 - 7 days / $ 5.66 / 0.5 lb"],
  },
  ebay: {
    department: ["Men", "Teens"],
    Type: ["T-Shirt"],
  },
};

describe("withDropdownOptions", () => {
  it("does not restore stale reference options after the native schema removes its list", () => {
    const fields = withDropdownOptions([{ key: "size", options: [], optionsSource: "marketplace" }], "general", FORMS);
    expect(fields[0].options).toEqual([]);
  });
  it("fills an empty Depop style list from the reference choices", () => {
    const fields = withDropdownOptions(
      [{ key: "depop_specifics.style", options: [], optionsSource: "marketplace" }],
      "depop",
      FORMS,
    );
    expect(fields[0].options).toEqual(["Streetwear", "Sportswear"]);
    expect(fields[0].optionsSource).toBe("reference");
  });
  it("does not offer a category-specific static eBay department list", () => {
    expect(optionsForField(FORMS, "ebay", "department")).toBeUndefined();
    const fields = withDropdownOptions([{ key: "ebay_specifics.department", label: "Department" }] as Field[], "ebay", FORMS);
    expect(fields[0].options).toBeUndefined();
    const live = withDropdownOptions([{ key: "ebay_specifics.department", options: ["Women"] }], "ebay", FORMS);
    expect(live[0].options).toEqual(["Women"]);
  });
  it("attaches general condition and size options from the vendoo form", () => {
    const input: Field[] = [
      { key: "condition", label: "Condition" },
      { key: "size", label: "Size" },
      { key: "brand", label: "Brand" },
    ];
    const fields = withDropdownOptions(input, "general", FORMS);
    expect(fields[0].options).toEqual(["New With Tags/Box", "Pre-Owned - Good"]);
    expect(fields[1].options).toEqual(["S", "M", "L"]);
    expect(fields[2].options).toBeUndefined();
  });

  it("matches marketplace field leaves case-insensitively", () => {
    const depopInput: Field[] = [
      { key: "depop_specifics.style", label: "Style" },
      { key: "depop_specifics.productType", label: "Product Type" },
      { key: "ebay_specifics.type", label: "Type" },
      { key: "mercari_specifics.shippingLabel", label: "Shipping Label" },
    ];
    const fields = withDropdownOptions(depopInput, "depop", FORMS);
    expect(fields[0].options).toEqual(["Streetwear", "Sportswear"]);
    expect(fields[1].options).toEqual(["jackets", "tshirts"]);

    const ebay = withDropdownOptions(
      [{ key: "ebay_specifics.type", label: "Type" }] as Field[],
      "ebay",
      FORMS,
    );
    expect(ebay[0].options).toEqual(["T-Shirt"]);

    const mercari = withDropdownOptions(
      [{ key: "mercari_specifics.shippingLabel", label: "Shipping Label" }] as Field[],
      "mercari",
      FORMS,
    );
    expect(mercari[0].options?.[0]).toContain("Ground Advantage");
  });

  it("keeps live schema options when already present", () => {
    const fields = withDropdownOptions(
      [{ key: "depop_specifics.style", label: "Style", options: ["Live Only"] }] as Field[],
      "depop",
      FORMS,
    );
    expect(fields[0].options).toEqual(["Live Only"]);
  });

  it("prefers the category schema's options in fix prompts", () => {
    const forms = { depop: { size: ["XS", "XL", "Other"], material: ["Cotton"] } };
    const schema = [{
      marketplace: "depop", category_id: "kidswear_jackets", known: true,
      fields: [{ key: "size", label: "Size", value: "", required: true, multi: false, selection_only: true,
        options: ["14 years", "16 years", "Other"] }],
    }];
    const merged = withSchemaOptions(forms, schema);
    expect(optionsForField(merged, "depop", "depop_specifics.size")).toEqual(["14 years", "16 years", "Other"]);
    expect(optionsForField(merged, "depop", "depop_specifics.material")).toEqual(["Cotton"]);
    expect(withSchemaOptions(forms, undefined)).toBe(forms);
  });
});
