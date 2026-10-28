import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { NativeInventoryEditor } from "./NativeInventoryEditor";

describe("native size inventory", () => {
  it("renders standard and custom sizes with separate quantities and SKUs without saving", () => {
    const html = renderToStaticMarkup(<NativeInventoryEditor
      groups={[{ id: "standard", label: "Standard", sizes: [{ id: "M", label: "Medium" }] },
        { id: "custom", label: "Custom size", sizes: [] }]}
      value={[{ sizeSetId: "standard", sizeId: "M", quantity: 2, sku: "MEDIUM-2" },
        { sizeSetId: "custom", sizeId: "Custom XL", quantity: 1, sku: "CUSTOM-1" }]}
      onChange={() => { throw new Error("Rendering must not save"); }} />);
    expect(html).toContain('aria-label="Size inventory"');
    expect(html).toContain('value="Medium"');
    expect(html).toContain('value="Custom XL"');
    expect(html).toContain('value="MEDIUM-2"');
    expect(html).toContain('value="CUSTOM-1"');
    expect(html).toContain('aria-label="Quantity 2"');
    expect(html).toContain('aria-label="Remove size 2"');
    expect(html).toContain("Add another size");
    expect(html).toContain(">Size<");
    expect(html).toContain(">Available<");
    expect(html).toContain(">Sold<");
  });
});
