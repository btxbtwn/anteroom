import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CategoryTreeSelect, groupedChoices, SearchableMultiSelect, SearchableSelect } from "./ListingFormControls";

describe("marketplace choice controls", () => {
  it("identifies an unsupported saved choice without silently clearing it", () => {
    const html = renderToStaticMarkup(<SearchableSelect id="size" fieldKey="size" value="Unknown size"
      options={["S", "M", "L"]} onChange={() => { throw new Error("Rendering must not save"); }} />);
    expect(html).toContain("Saved value");
    expect(html).toContain("Unknown size");
    expect(html).toContain('aria-invalid="true"');
    expect(html).toContain('role="alert"');
  });

  it("permits native free-text values outside the suggestions", () => {
    const html = renderToStaticMarkup(<SearchableSelect id="brand" fieldKey="brand" value="Independent maker"
      options={["Nike"]} selectionOnly={false} onChange={() => {}} />);
    expect(html).not.toContain("Saved value");
    expect(html).not.toContain('aria-invalid="true"');
  });

  it("does not declare a value invalid only because a reference list omits it", () => {
    const html = renderToStaticMarkup(<SearchableSelect id="condition" fieldKey="condition"
      value="Pre-Owned - Good" options={["Pre-owned"]} choicesVerified={false} onChange={() => {}} />);
    expect(html).toContain("outside these reference choices");
    expect(html).not.toContain('aria-invalid="true"');
  });

  it("shows every selected value and permits correction of unavailable entries", () => {
    const html = renderToStaticMarkup(<SearchableMultiSelect id="season" fieldKey="ebay_specifics.season"
      value={["Spring", "Summer", "Obsolete"]} options={["Spring", "Summer", "Winter"]} maxValues={2}
      onChange={() => { throw new Error("Rendering must not save"); }} />);
    expect(html).toContain('aria-label="Remove Spring"');
    expect(html).toContain('aria-label="Remove Summer"');
    expect(html).toContain('aria-label="Remove Obsolete"');
    expect(html).toContain("Unavailable saved values: Obsolete");
    expect(html).toContain("Choose at most 2 values.");
    const field = html.slice(html.indexOf('data-slot="multi-select"'), html.indexOf('data-slot="combobox-popup"'));
    expect(field).toContain('aria-label="Remove Spring"');
    expect(field).toContain('data-listing-field="ebay_specifics.season"');
  });

  it("puts the hint and the selected count on one line under the field", () => {
    const html = renderToStaticMarkup(<SearchableMultiSelect id="occasion" fieldKey="depop_specifics.occasion"
      value={["Casual"]} options={["Casual", "Going out", "Holiday"]} maxValues={3} help="Add up to 3"
      onChange={() => { throw new Error("Rendering must not save"); }} />);
    expect(html).toContain('id="occasion-help"');
    expect(html).toContain("Add up to 3");
    expect(html).toContain('aria-label="1 of 3 selected"');
    expect(html).toContain(">1/3<");
    expect(html).toContain('aria-describedby="occasion-help"');
    const field = html.slice(html.indexOf('data-slot="multi-select"'), html.indexOf('id="occasion-help"'));
    expect(field).toContain('aria-label="Clear values"');
  });

  it("offers a clear control for a saved Depop style", () => {
    const html = renderToStaticMarkup(<SearchableMultiSelect id="style" fieldKey="depop_specifics.style"
      value={["Casual", "Minimalist", "Indie"]}
      options={["Casual", "Minimalist", "Indie", "Streetwear"]}
      onChange={() => { throw new Error("Rendering must not save"); }} />);
    expect(html).toContain('aria-label="Clear values"');
    expect(html).toContain("Casual");
    expect(html).toContain("Minimalist");
    expect(html).toContain("Indie");
  });
});

describe("category tree dropdown", () => {
  const ignore = () => { throw new Error("Rendering must not save"); };

  it("opens the next level once a first-level category is saved", () => {
    const html = renderToStaticMarkup(<CategoryTreeSelect id="category" fieldKey="sellwild_specifics.nativeCategoryId"
      value="Fashion > Shoes" choices={[
        { label: "Fashion > Shoes", id: "69" },
        { label: "Fashion > Tops", id: "70" },
        { label: "Home & Garden > Furniture", id: "61" },
      ]} onChange={ignore} />);
    expect(html.match(/<select/g)).toHaveLength(2);
    expect(html).toContain(">Fashion</option>");
    expect(html).toContain(">Home &amp; Garden</option>");
    expect(html).toContain(">Shoes</option>");
    expect(html).not.toContain("<optgroup");
    expect(html).toContain('data-listing-field="sellwild_specifics.nativeCategoryId"');
    expect(html).not.toContain("continue until the last one");
  });

  it("shows department, group and item as three dropdowns", () => {
    const html = renderToStaticMarkup(<CategoryTreeSelect id="category" fieldKey="direct_category_ids.depop"
      value="Men > Bottoms > Jeans" choices={[
        { label: "Men > Bottoms > Jeans", id: "menswear_jeans" },
        { label: "Men > Tops > T-shirts", id: "menswear_tshirts" },
        { label: "Women > Dresses > Mini", id: "womenswear_mini" },
      ]} onChange={ignore} />);
    expect(html.match(/<select/g)).toHaveLength(3);
    expect(html).toContain(">Men</option>");
    expect(html).toContain(">Women</option>");
    expect(html).toContain(">Bottoms</option>");
    expect(html).toContain(">Jeans</option>");
    expect(html).toContain('aria-label="Next category"');
    expect(html).not.toContain("continue until the last one");
  });

  it("does not ask for another step when every row in the list is a last step", () => {
    const html = renderToStaticMarkup(<CategoryTreeSelect id="category" fieldKey="sellwild_specifics.nativeCategoryId"
      value="" choices={[
        { label: "Shoes", id: "69" },
        { label: "Furniture", id: "61" },
      ]} onChange={ignore} />);
    expect(html).not.toContain("continue until the last one");
    expect(html).toContain(">Shoes</option>");
  });

  it("asks the seller to continue when no category is chosen yet", () => {
    const html = renderToStaticMarkup(<CategoryTreeSelect id="category" fieldKey="direct_category_ids.mercari"
      value="" choices={[
        { label: "Kids > Girls tops > Girls (4+)", id: "1911" },
      ]} onChange={ignore} />);
    expect(html).toContain("Choose a category group, then continue until the last one.");
    expect(html).toContain(">Kids</option>");
    expect(html.match(/<select/g)).toHaveLength(1);
  });
});

describe("groupedChoices", () => {
  it("keeps the marketplace's order and starts a heading wherever it changes", () => {
    expect(groupedChoices(["XS (0-2)", "S (4-6)", "XS Petite", "30", "One size"], {
      "XS (0-2)": "Standard", "S (4-6)": "Standard", "XS Petite": "Petite", "30": "Inch",
    }).map((group) => [group.value, group.items])).toEqual([
      ["Standard", ["XS (0-2)", "S (4-6)"]], ["Petite", ["XS Petite"]], ["Inch", ["30"]], ["", ["One size"]],
    ]);
  });
});
