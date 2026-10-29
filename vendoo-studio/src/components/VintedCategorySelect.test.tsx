import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { VintedCategorySelect } from "./VintedCategorySelect";

vi.mock("../api/client", () => ({
  api: { nativeMarketplaces: { vintedCategories: vi.fn() } },
}));

describe("Vinted category picker", () => {
  it("shows Vinted's own breadcrumbs in the shared tree picker", () => {
    const client = new QueryClient();
    client.setQueryData(["vinted-categories", "conversation"], {
      options: ["Women > Clothing > Jeans > Straight jeans", "Men > Clothing > Jeans"],
    });
    const html = renderToStaticMarkup(
      <QueryClientProvider client={client}>
        <VintedCategorySelect convId="conversation" id="category" fieldKey="vinted_specifics.category"
          value="Women > Clothing > Jeans > Straight jeans" onChange={() => {}} />
      </QueryClientProvider>,
    );
    expect(html).toContain("Women → Clothing → Jeans → Straight jeans");
    expect(html).toContain('data-listing-field="vinted_specifics.category"');
    expect(html).not.toContain("<input");
  });
});
