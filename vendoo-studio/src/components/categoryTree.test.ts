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
  it("groups the first dropdown by the top category and stops at a leaf", () => {
    const menu = categoryMenu([
      { label: "Fashion > Shoes", id: "69" },
      { label: "Fashion > Tops", id: "70" },
      { label: "Home & Garden > Furniture", id: "61" },
    ], []);
    expect(menu?.groups.map((group) => group.heading)).toEqual(["Fashion", "Home & Garden"]);
    expect(menu?.groups[0].items.map((item) => [item.label, item.terminal])).toEqual([
      ["Shoes", true],
      ["Tops", true],
    ]);
    expect(categorySteps([
      { label: "Fashion > Shoes", id: "69" },
      { label: "Fashion > Tops", id: "70" },
    ], ["Fashion", "Shoes"])).toHaveLength(1);
    expect(categoryChoiceAt([
      { label: "Fashion > Shoes", id: "69" },
    ], ["Fashion", "Shoes"])?.id).toBe("69");
  });

  it("opens the next dropdown when the chosen category still has children", () => {
    const first = categoryMenu(depop, []);
    const shirts = first?.groups.find((group) => group.heading === "Men")?.items.find((item) => item.label === "Bottoms");
    expect(shirts?.terminal).toBe(false);
    const next = categoryMenu(depop, ["Men", "Bottoms"]);
    expect(next?.groups.map((group) => group.items.map((item) => item.label))).toEqual([["Jeans"]]);
    expect(next?.groups[0].items[0].terminal).toBe(true);
    const steps = categorySteps(depop, ["Men", "Bottoms", "Jeans"]);
    expect(steps.map((step) => step.value)).toEqual(["Men > Bottoms", "Men > Bottoms > Jeans"]);
    expect(categoryChoiceAt(depop, ["Men", "Bottoms"])).toBeUndefined();
    expect(categoryChoiceAt(depop, ["Men", "Bottoms", "Jeans"])?.id).toBe("menswear_jeans");
  });

  it("lets a category be chosen on its own or through a narrower leaf", () => {
    const choices = [
      { label: "Men > Shirts > None", id: "men/shirts" },
      { label: "Men > Shirts > Tees", id: "men/shirts/tees" },
      { label: "Men > Shoes", id: "men/shoes" },
    ];
    const root = categoryMenu(choices, []);
    const men = root?.groups[0].items;
    expect(men?.map((item) => [item.label, item.terminal])).toEqual([
      ["Shirts", false],
      ["Shoes", true],
    ]);
    expect(categoryMenu(choices, ["Men", "Shirts"])?.groups[0].items.map((item) => item.label)).toEqual(["None", "Tees"]);
    expect(categorySteps(choices, ["Men", "Shirts", "Tees"]).map((step) => step.value)).toEqual([
      "Men > Shirts",
      "Men > Shirts > Tees",
    ]);
  });

  it("keeps a category that is itself a choice beside the rows under it", () => {
    const menu = categoryMenu([
      { label: "Men", id: "men" },
      { label: "Men > Shirts > Tees", id: "men/shirts/tees" },
    ], []);
    expect(menu?.groups[0].items.map((item) => [item.label, item.terminal])).toEqual([
      ["Men", true],
      ["Shirts", false],
    ]);
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
