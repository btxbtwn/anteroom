import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GeneralCategorySelect } from "./ListingFormControls";

vi.mock("../api/client", () => ({
  api: { catalog: { categories: vi.fn() } },
}));

const tee = "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts";

function html(tree: unknown, value: string) {
  const client = new QueryClient();
  client.setQueryData(["catalog-categories", "general"], tree);
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <GeneralCategorySelect id="cat" fieldKey="category_path" value={value} onChange={() => {}}
        fallback={<input aria-label="Typed category" defaultValue={value} />} />
    </QueryClientProvider>,
  );
}

describe("General category picker", () => {
  it("offers the shared category tree as a dropdown showing the saved path", () => {
    const out = html({ coverage: "tree", complete: true, nodes: [
      { id: "clothing", path: "Clothing, Shoes & Accessories", label: "Clothing, Shoes & Accessories", is_leaf: false, has_children: true },
      { id: "tees", path: tee, label: "T-Shirts", is_leaf: true, has_children: false },
    ] }, tee);
    expect(out).toContain("Clothing, Shoes &amp; Accessories → Men → Men&#x27;s Clothing → Shirts → T-Shirts");
    expect(out).toContain('data-listing-field="category_path"');
    expect(out).not.toContain("Typed category");
  });

  it("keeps a text box when the category tree has not synced", () => {
    const out = html({ coverage: "observed", complete: false, nodes: [] }, "Men > Shirts");
    expect(out).toContain('aria-label="Typed category"');
  });
});
