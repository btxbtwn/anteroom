import { describe, expect, it } from "vitest";
import {
  catalogLeafChoices,
  categoryChoiceAt,
  categoryMenu,
  categorySearch,
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
    expect(pathCategoryChoices({
      key: "sellwild_specifics.nativeCategoryId",
      label: "Category",
      options: ["Vehicles | Cars"],
      optionValues: { "Vehicles | Cars": "43" },
    })).toEqual([{ label: "Vehicles | Cars", id: "43" }]);
    expect(categoryMenu([{ label: "Vehicles | Cars", id: "43" }], [])?.items.map((item) => item.label)).toEqual(["Vehicles"]);
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

describe("category search", () => {
  const choices = [
    { label: "Clothing, Shoes & Accessories > Women > Pants", id: "pants" },
    { label: "Clothing, Shoes & Accessories > Women > Jeans", id: "jeans" },
    { label: "Clothing, Shoes & Accessories > Men > Jeans", id: "mens-jeans" },
    { label: "Home & Garden > Furniture", id: "furniture" },
  ];

  it("finds a category anywhere in its path and names the whole path", () => {
    expect(categorySearch(choices, "women jeans").map((item) => [item.label, item.path, item.terminal])).toEqual([
      ["Clothing, Shoes & Accessories > Women > Jeans", ["Clothing, Shoes & Accessories", "Women", "Jeans"], true],
    ]);
    expect(categorySearch(choices, "womens jeans").map((item) => item.label)).toEqual([
      "Clothing, Shoes & Accessories > Women > Jeans",
    ]);
  });

  it("puts a match in the category name ahead of a match in an ancestor", () => {
    expect(categorySearch(choices, "jeans").map((item) => item.label)).toEqual([
      "Clothing, Shoes & Accessories > Men > Jeans",
      "Clothing, Shoes & Accessories > Women > Jeans",
    ]);
    expect(categorySearch(choices, "clothing").map((item) => item.label)).toHaveLength(3);
  });

  it("does nothing for an empty query and stops at the limit", () => {
    expect(categorySearch(choices, "  ")).toEqual([]);
    expect(categorySearch(choices, "clothing", 2)).toHaveLength(2);
  });
});
