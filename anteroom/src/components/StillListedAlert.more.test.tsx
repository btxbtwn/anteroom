// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { StillListedItem } from "../api/directConnections";
import { StillListedAlert } from "./StillListedAlert";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => { vi.restoreAllMocks(); document.body.innerHTML = ""; });

const item = (n: number): StillListedItem => ({
  conversationId: `item-${n}`, title: `Coat ${n}`, saleId: `sale-${n}`, soldOn: "poshmark", soldAt: null, detected: true, pinned: true,
  stillListed: [{ marketplace: "ebay", listingId: `e${n}`, followed: true, state: "failed", error: null, at: null, attempts: 1, url: null }],
});

it("every sold item still listed can be opened from the alert, past the first three", async () => {
  vi.spyOn(api.marketplaceSales, "stillListed").mockResolvedValue({ autodelist: true, items: [1, 2, 3, 4, 5].map(item) });
  const opened: string[] = [];
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => root.render(
    <QueryClientProvider client={client}><StillListedAlert onOpen={(id) => opened.push(id)} /></QueryClientProvider>,
  ));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(host.textContent).toContain("5 sold items are still listed");
  expect(host.textContent).not.toContain("Coat 5");
  const more = [...host.querySelectorAll("button")].find((button) => button.textContent === "Show 2 more");
  expect(more).toBeDefined();
  await act(async () => more!.click());
  const fifth = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("Coat 5"));
  await act(async () => fifth!.click());
  expect(opened).toEqual(["item-5"]);
  await act(async () => root.unmount());
});
