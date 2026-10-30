import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { MarketplaceForm, MarketplaceFormField } from "../api/types";
import { NativeFormPreview } from "./MarketplacePreview";
const row = (key: string, label: string, value: MarketplaceFormField["value"], extra = {}): MarketplaceFormField => ({ key, label, value, required: false, multi: false, selection_only: false, options: [], ...extra });

describe("native Poshmark preview", () => {
  it("uses only native rows, preserves sections/widths and escapes listing text", () => {
    const form: MarketplaceForm = { marketplace: "poshmark", native: true, known: true, category_id: "men/shirts", fields: [
      row("poshmark_specifics.title", "Title", "<script>private</script>", { section: "Title" }),
      row("price", "Listing Price", 42, { section: "Price", width: "half", prefix: "$" }),
      row("poshmark_specifics.shippingDiscountId", "Discounted Shipping", "free", { section: "Price", options: ["Free shipping"], option_values: { "Free shipping": "free" } }),
    ] };
    const html = renderToStaticMarkup(<NativeFormPreview form={form}/>);
    expect(html).toContain("&lt;script&gt;private&lt;/script&gt;");
    expect(html).toContain("width-half");
    expect(html).toContain("$42");
    expect(html).toContain("Free shipping");
    expect(html.match(/<output>/g)).toHaveLength(3);
    expect(html).not.toContain("Publish");
    expect(html).not.toContain("Save draft");
  });
  it("shares the desktop rules for conditional Drop Time and Lowest Price rows", () => {
    const form: MarketplaceForm = { marketplace: "poshmark", native: true, known: true, category_id: "", fields: [
      row("poshmark_specifics.availability", "Availability", "available", { section: "Availability" }),
      row("poshmark_specifics.makeAvailableAt", "Drop Time", "", { section: "Availability" }),
      row("poshmark_specifics.smartSell.enabled", "Smart Sell", false, { section: "Price" }),
      row("poshmark_specifics.smartSell.minimumPrice", "Lowest Price", "", { section: "Price" }),
    ] };
    let html = renderToStaticMarkup(<NativeFormPreview form={form}/>);
    expect(html).not.toContain("Drop Time"); expect(html).not.toContain("Lowest Price");
    form.fields[0].value = "coming_soon"; form.fields[2].value = true;
    html = renderToStaticMarkup(<NativeFormPreview form={form}/>);
    expect(html).toContain("Drop Time"); expect(html).toContain("Lowest Price");
  });
});
