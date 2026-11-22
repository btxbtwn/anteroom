// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { api } from "../api/client";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
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

it("pins edited feedback to its original revision and provides Retry after a read failure", async () => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  vi.spyOn(api.generationFeedback, "get")
    .mockRejectedValueOnce(new Error("Could not read feedback"))
    .mockResolvedValue({ revision_id: "original", feedback: null });
  const save = vi.spyOn(api.generationFeedback, "save").mockRejectedValue(new Error("A newer generation is available"));
  async function click(label: string) {
    const button = [...document.querySelectorAll("button")].find(
      (item) => (item.getAttribute("aria-label") || item.textContent?.trim()) === label,
    );
    expect(button).toBeDefined();
    await act(async () => { button?.click(); });
  }
  try {
    await act(async () => {
      root.render(<QueryClientProvider client={client}>
        <GenerationFeedback id="item" revision="edited-revision" />
      </QueryClientProvider>);
    });
    await act(async () => { await vi.waitFor(() => expect(client.getQueryState(["generation-feedback", "item", "edited-revision"])?.status).toBe("error")); });
    expect(container.textContent).toContain("Could not read feedback");
    await click("Retry");
    await act(async () => { await vi.waitFor(() => expect(client.getQueryState(["generation-feedback", "item", "edited-revision"])?.status).toBe("success")); });
    await click("Thumbs down");
    await act(async () => {
      client.setQueryData(["generation-feedback", "item", "edited-revision"], { revision_id: "newer", feedback: null });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await click("Wrong size");
    await click("Save feedback");
    expect(save).toHaveBeenCalledWith("item", "original", { rating: "down", issues: ["size"], note: "" });
  } finally {
    await act(async () => { root.unmount(); });
    client.clear();
    container.remove();
    vi.restoreAllMocks();
  }
});
