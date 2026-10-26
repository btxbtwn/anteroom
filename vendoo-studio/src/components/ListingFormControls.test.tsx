import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SearchableMultiSelect, SearchableSelect } from "./ListingFormControls";

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
  });
});
