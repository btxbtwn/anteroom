import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { GenerationFeedback, GenerationFeedbackSummary } from "./GenerationFeedback";

it("shows the saved rating on the generated revision and recent issue totals", () => {
  const client = new QueryClient();
  client.setQueryData(["generation-feedback", "item", "edited-revision"], {
    revision_id: "generated-revision", feedback: { rating: "down", issues: ["size"], note: "Private note" },
  });
  client.setQueryData(["generation-feedback", "summary"], { up: 4, down: 1, issues: { size: 1 } });
  const html = renderToStaticMarkup(<QueryClientProvider client={client}>
    <GenerationFeedback id="item" revision="edited-revision" />
    <GenerationFeedbackSummary />
  </QueryClientProvider>);
  expect(html).toMatch(/<button[^>]*aria-label="Thumbs down"[^>]*aria-pressed="true"/);
  expect(html).toContain("4 thumbs up · 1 thumbs down");
  expect(html).toContain("Wrong size · 1");
  expect(html).not.toContain("Private note");
});
