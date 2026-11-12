// @vitest-environment happy-dom
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { PriceDropPreview } from "../api/types";
import { BulkRegeneratePriceDialog } from "./BulkRegeneratePriceDialog";
import { RegeneratePriceDialog } from "./RegeneratePriceDialog";

vi.mock("../api/client", () => ({ api: { listings: {
  priceDropPreview: vi.fn(), applyPriceDrop: vi.fn(), priceDropComps: vi.fn(),
} } }));
vi.mock("./PriceDropParts", () => ({
  money: (value: number) => `$${value}`,
  PriceDialog: ({ children, footer }: { children: ReactNode; footer: ReactNode }) => <div>{children}{footer}</div>,
  PriceSection: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  PriceRows: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  PriceRow: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  PriceStatus: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  PriceChip: ({ children, onClick }: { children: ReactNode; onClick: () => void }) => <button onClick={onClick}>{children}</button>,
}));
vi.mock("./ui/button", () => ({ Button: ({ children, disabled, onClick }: {
  children: ReactNode; disabled?: boolean; onClick: () => void;
}) => <button disabled={disabled} onClick={onClick}>{children}</button> }));
vi.mock("./ui/toast", () => ({ toastManager: { add: vi.fn() } }));

function preview(revision: string, price: number): PriceDropPreview {
  return {
    revision_id: revision, current_price: price, first_price: price, age_days: null,
    sell_through: null, suggested_price: price * .9, suggested_percent: 10,
    suggested_effective_percent: 10, suggested_mode: "percent", suggested_reason: "Suggestion",
    percent_options: [10], prices_by_percent: { "10": price * .9 },
    drop_options: [{ price: price * .8, percent: 20, effective_percent: 20 }],
    comps: { available: false, text: "", market: "", market_midpoint: null, target_price: null, source: "", query: "" },
    history: [],
  };
}

const hosts: { host: HTMLDivElement; root: ReturnType<typeof createRoot> }[] = [];
function mount(element: ReactNode) {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  hosts.push({ host, root });
  act(() => root.render(element));
  return host;
}
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); }); }
function click(host: HTMLDivElement, label: string) {
  const button = [...host.querySelectorAll("button")].find((node) => node.textContent === label);
  expect(button).toBeDefined();
  act(() => button!.click());
}
afterEach(() => {
  for (const { host, root } of hosts.splice(0)) { act(() => root.unmount()); host.remove(); }
  vi.resetAllMocks();
});

it("bulk percentages use the reviewed current price and carry its revision, never the sidebar price", async () => {
  vi.mocked(api.listings.priceDropPreview).mockResolvedValue(preview("new", 60));
  const confirm = vi.fn();
  const host = mount(<BulkRegeneratePriceDialog listings={[{ id: "a", title: "A", price: 40 }]} onClose={() => {}} onConfirm={confirm} />);
  click(host, "−10% each");
  expect([...host.querySelectorAll("button")].find((button) => button.textContent === "Rewrite")?.disabled).toBe(true);
  await settle();
  click(host, "Drop and rewrite");
  expect(confirm.mock.calls[0][0].get("a")).toEqual({ price: 54, percent: 10, mode: "percent", expected_revision_id: "new" });
});

it("a picked single price keeps its original revision after a background preview refresh", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["price-drop-preview", "a"], preview("reviewed", 40));
  vi.mocked(api.listings.applyPriceDrop).mockRejectedValue(new Error("This listing changed since you reviewed it"));
  const rewrite = vi.fn();
  const host = mount(<QueryClientProvider client={client}><RegeneratePriceDialog convId="a" open onClose={() => {}} onRewrite={rewrite} /></QueryClientProvider>);
  await settle();
  click(host, "$32 · −20%");
  act(() => client.setQueryData(["price-drop-preview", "a"], preview("newer", 60)));
  await settle();
  click(host, "Confirm");
  await settle();
  expect(api.listings.applyPriceDrop).toHaveBeenCalledWith("a", { price: 32, percent: 20, mode: "percent", expected_revision_id: "reviewed" });
  expect(rewrite).not.toHaveBeenCalled();
  client.clear();
});
