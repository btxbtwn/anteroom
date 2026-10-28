import { describe, expect, it } from "vitest";
import {
  catalogLeafChoices,
  categoryChoiceAt,
  categoryMenu,
  categorySteps,
  pathCategoryChoices,
} from "./categoryTree";

const depop = [
  { label: "Men > Bottoms > Jeans", id: "menswear_jeans" },
  { label: "Men > Tops > T-shirts", id: "menswear_tshirts" },
  { label: "Women > Dresses > Mini", id: "womenswear_mini" },
];

describe("category tree menus", () => {
  it("shows one level per dropdown and stops at a leaf", () => {
    const choices = [
      { label: "Fashion > Shoes", id: "69" },
      { label: "Fashion > Tops", id: "70" },
      { label: "Home & Garden > Furniture", id: "61" },
    ];
    expect(categoryMenu(choices, [])?.items.map((item) => [item.label, item.terminal])).toEqual([
      ["Fashion", false],
      ["Home & Garden", false],
    ]);
    expect(categoryMenu(choices, ["Fashion"])?.items.map((item) => [item.label, item.terminal])).toEqual([
      ["Shoes", true],
      ["Tops", true],
    ]);
    expect(categorySteps(choices, ["Fashion", "Shoes"]).map((step) => step.value)).toEqual([
      "Fashion",
      "Fashion > Shoes",
    ]);
    expect(categoryChoiceAt(choices, ["Fashion", "Shoes"])?.id).toBe("69");
  });

  it("starts with the department, then the group, then the item", () => {
    expect(categoryMenu(depop, [])?.items.map((item) => item.label)).toEqual(["Men", "Women"]);
    expect(categoryMenu(depop, ["Men"])?.items.map((item) => [item.label, item.terminal])).toEqual([
      ["Bottoms", false],
      ["Tops", false],
    ]);
    expect(categoryMenu(depop, ["Men", "Bottoms"])?.items.map((item) => [item.label, item.terminal])).toEqual([
      ["Jeans", true],
    ]);
    expect(categorySteps(depop, ["Men", "Bottoms", "Jeans"]).map((step) => step.value)).toEqual([
      "Men",
      "Men > Bottoms",
      "Men > Bottoms > Jeans",
    ]);
    expect(categorySteps(depop, ["Men"]).map((step) => step.value)).toEqual(["Men", ""]);
    expect(categoryChoiceAt(depop, ["Men", "Bottoms"])).toBeUndefined();
    expect(categoryChoiceAt(depop, ["Men", "Bottoms", "Jeans"])?.id).toBe("menswear_jeans");
  });

  it("lets a category be chosen on its own or through a narrower leaf", () => {
    const choices = [
      { label: "Men > Shirts > None", id: "men/shirts" },
      { label: "Men > Shirts > Tees", id: "men/shirts/tees" },
      { label: "Men > Shoes", id: "men/shoes" },
    ];
    expect(categoryMenu(choices, ["Men"])?.items.map((item) => [item.label, item.terminal])).toEqual([
      ["Shirts", false],
      ["Shoes", true],
    ]);
    expect(categoryMenu(choices, ["Men", "Shirts"])?.items.map((item) => item.label)).toEqual(["None", "Tees"]);
    expect(categorySteps(choices, ["Men", "Shoes"]).map((step) => step.value)).toEqual(["Men", "Men > Shoes"]);
  });

  it("keeps a category that is itself a choice open for narrower rows", () => {
    const choices = [
      { label: "Men", id: "men" },
      { label: "Men > Shirts > Tees", id: "men/shirts/tees" },
    ];
    expect(categoryMenu(choices, [])?.items.map((item) => [item.label, item.terminal])).toEqual([["Men", false]]);
    expect(categoryChoiceAt(choices, ["Men"])?.id).toBe("men");
  });

  it("uses path-shaped category options and leaves other fields alone", () => {
    expect(pathCategoryChoices({
      key: "direct_category_ids.depop",
      label: "Category",
      options: ["Men > Bottoms > Jeans"],
      optionValues: { "Men > Bottoms > Jeans": "menswear_jeans" },
    })).toEqual([{ label: "Men > Bottoms > Jeans", id: "menswear_jeans" }]);
    expect(pathCategoryChoices({
      key: "direct_category_ids.ebay",
      label: "Category",
      options: ["Women > Tees"],
    })).toBeNull();
    expect(pathCategoryChoices({
      key: "depop_specifics.condition",
      label: "Condition",
      options: ["Good"],
    })).toBeNull();
  });

  it("reads leaves from a synced category tree", () => {
    expect(catalogLeafChoices({
      coverage: "tree",
      nodes: [
        { id: "women", parent_id: "__root", label: "Women", path: "Women", is_leaf: false, has_children: true },
        { id: "tees", parent_id: "women", label: "Tees", path: "Women > Tees", is_leaf: true, has_children: false },
      ],
    })).toEqual([{ label: "Women > Tees", id: "tees" }]);
    expect(catalogLeafChoices({ coverage: "observed", nodes: [] })).toEqual([]);
  });
});
