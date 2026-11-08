import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it } from "vitest";
import { SuggestionsPage } from "./SuggestionsPage";

it("explains an empty feed without showing a relisting action", () => {
  const client = new QueryClient();
  client.setQueryData(["suggestions"], { suggestions: [] });
  const html = renderToStaticMarkup(<QueryClientProvider client={client}><SuggestionsPage active onOpenListing={() => {}} /></QueryClientProvider>);
  expect(html).toContain("No suggestions right now.");
  expect(html).toContain('aria-label="Suggested listings"');
  expect(html).not.toContain("Review for relisting");
});

it("shows a named loading state while the initial feed is pending", () => {
  const client = new QueryClient();
  const html = renderToStaticMarkup(<QueryClientProvider client={client}><SuggestionsPage active onOpenListing={() => {}} /></QueryClientProvider>);
  expect(html).toContain('aria-label="Loading suggestions"');
  expect(html).not.toContain("No suggestions right now.");
});
