import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { GeneralFields } from "./GeneralFields";

it("renders saved listing values as text without saving or adding marketplace actions", () => {
  const html = renderToStaticMarkup(<GeneralFields values={{ description: "<script>private notes</script>", price: 42, quantity: 2, sku: "COAT-1" }} disabled={false}
    onChange={() => { throw new Error("Rendering must not save"); }}/>);
  expect(html).toContain("&lt;script&gt;private notes&lt;/script&gt;");
  expect(html).toContain('value="42"');
  expect(html).toContain('value="COAT-1"');
  expect(html).not.toContain("Publish");
});

describe("disabled listing form", () => {
  it("disables all sections while photos are being saved", () => {
    const html = renderToStaticMarkup(<GeneralFields values={{}} disabled onChange={() => {}}/>);
    expect(html.match(/<fieldset[^>]*disabled=""/g)?.length).toBeGreaterThan(1);
  });
});
