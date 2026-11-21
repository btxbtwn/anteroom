import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it } from "vitest";
import { SuggestionsPage } from "./SuggestionsPage";
import { useBulkRegenerate } from "./useBulkRegenerate";

function Page() {
  const bulk = useBulkRegenerate(() => false);
  return <SuggestionsPage active onOpenListing={() => {}} conversations={undefined} bulk={bulk} />;
}

it("explains an empty feed without showing a relisting action", () => {
  const client = new QueryClient();
  client.setQueryData(["suggestions"], { suggestions: [] });
  const html = renderToStaticMarkup(<QueryClientProvider client={client}><Page /></QueryClientProvider>);
  expect(html).toContain("No suggestions right now.");
  expect(html).toContain('aria-label="Suggested listings"');
  expect(html).not.toContain("Review for relisting");
});

it("shows a named loading state while the initial feed is pending", () => {
  const client = new QueryClient();
  const html = renderToStaticMarkup(<QueryClientProvider client={client}><Page /></QueryClientProvider>);
  expect(html).toContain('aria-label="Loading suggestions"');
  expect(html).not.toContain("No suggestions right now.");
});

it("offers regeneration selection beside a suggested listing with a photo", () => {
  const client = new QueryClient();
  client.setQueryData(["suggestions"], { suggestions: [{
    conversation_id: "coat", title: "Wool coat", reason: "Listed over 60 days.",
    kind: "stale_active", cover_photo_url: "/cover.png",
  }] });
  const html = renderToStaticMarkup(<QueryClientProvider client={client}><Page /></QueryClientProvider>);
  expect(html).toContain('aria-label="Select Wool coat"');
  expect(html).toContain("Regenerate selected");
});
