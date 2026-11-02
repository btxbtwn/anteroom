import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { VintedCategorySelect } from "./VintedCategorySelect";
import { desktopRuntime, WorkspaceRuntimeProvider } from "../app/runtime";

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

  it("on the web, sends the seller to sign in to Vinted in their own Chrome", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retryOnMount: false } } });
    const query = client.getQueryCache().build(client, { queryKey: ["vinted-categories", "conversation"] });
    query.setState({ ...query.state, status: "error", error: new Error("Sign in to Vinted in Chrome, then reload Vinted's choices.") });
    const html = renderToStaticMarkup(
      <WorkspaceRuntimeProvider value={{ ...desktopRuntime, hosted: true }}>
        <QueryClientProvider client={client}>
          <VintedCategorySelect convId="conversation" id="category" fieldKey="vinted_specifics.category" value="" onChange={() => {}} />
        </QueryClientProvider>
      </WorkspaceRuntimeProvider>,
    );
    expect(html).toContain("Sign in to Vinted in Chrome</a>");
    expect(html).toContain('href="https://www.vinted.com/"');
    expect(html).not.toContain("in Anteroom");
  });
});
