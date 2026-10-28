import { describe, expect, it } from "vitest";
import {
  catalogLeafChoices,
  categoryPathLabel,
  matchCategoryChoices,
  pathCategoryChoices,
} from "./categoryTree";

const depop = [
  { label: "Men > Bottoms > Jeans", id: "menswear_jeans" },
  { label: "Men > Tops > T-shirts", id: "menswear_tshirts" },
  { label: "Women > Dresses > Mini", id: "womenswear_mini" },
];

describe("category path search", () => {
  it("shows a stored path with the picker's separator", () => {
    expect(categoryPathLabel("Men > Bottoms > Jeans")).toBe("Men › Bottoms › Jeans");
    expect(categoryPathLabel("Men › Bottoms›Jeans")).toBe("Men › Bottoms › Jeans");
  });

  it("matches every typed word at the start of a word in the path", () => {
    expect(matchCategoryChoices(depop, "men jeans").map((choice) => choice.id)).toEqual(["menswear_jeans"]);
    expect(matchCategoryChoices(depop, "t-shirt").map((choice) => choice.id)).toEqual(["menswear_tshirts"]);
    expect(matchCategoryChoices(depop, "mini").map((choice) => choice.id)).toEqual(["womenswear_mini"]);
    expect(matchCategoryChoices(depop, "eans")).toEqual([]);
  });

  it("returns everything for an empty search, up to the limit, in the order given", () => {
    expect(matchCategoryChoices(depop, "")).toEqual(depop);
    expect(matchCategoryChoices(depop, " ", 2)).toEqual(depop.slice(0, 2));
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
