// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { LogsSettings } from "./LogsSettings";

it("describes an empty current list without denying errors that were cleared", () => {
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  client.setQueryData(["error-logs"], { entries: [] });
  try {
    const html = renderToStaticMarkup(<QueryClientProvider client={client}><LogsSettings /></QueryClientProvider>);
    expect(html).not.toContain("No warnings or errors since Anteroom started");
    expect(html).toContain("No warnings or errors in this list");
    expect(html).toContain("Clear or restart empties this list");
  } finally {
    client.clear();
  }
});
