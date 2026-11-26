// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { desktopRuntime, WorkspaceRuntimeProvider } from "../../app/runtime";
import { readConfirmDialogState, registerConfirmDialogHost, respondToConfirmDialog, completeConfirmDialogClose } from "../../ui/confirmDialog";
import { ConnectionsSettings } from "./ConnectionsSettings";

vi.mock("../../api/client", async (original) => {
  const module = await original<typeof import("../../api/client")>();
  const groups = new Map<PropertyKey, Record<PropertyKey, unknown>>();
  // Any call this test doesn't answer stays loading.
  const group = () => new Proxy({} as Record<PropertyKey, unknown>, {
    get: (target, key) => (target[key] ??= vi.fn(() => new Promise(() => {}))),
  });
  return {
    ...module,
    api: new Proxy({}, { get: (_, key) => { if (!groups.has(key)) groups.set(key, group()); return groups.get(key); } }),
  };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let queries: QueryClient;
let unregister: () => void;
let detection: { enabled: Record<string, boolean>; autodelist: boolean; auto_adjust_quantity: boolean };

async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

async function render(hosted = false) {
  await act(async () => {
    root.render(<QueryClientProvider client={queries}>
      <WorkspaceRuntimeProvider value={{ ...desktopRuntime, hosted }}><ConnectionsSettings /></WorkspaceRuntimeProvider>
    </QueryClientProvider>);
  });
  await settle();
}

function switchNamed(label: string) {
  const match = container.querySelector<HTMLElement>(`[aria-label="${label}"]`);
  if (!match) throw new Error(`Switch missing: ${label}`);
  return match;
}

function button(label: string) {
  const match = Array.from(container.querySelectorAll("button")).find((element) => element.textContent?.trim() === label);
  if (!match) throw new Error(`Button missing: ${label}`);
  return match;
}

async function click(element: HTMLElement) {
  await act(async () => { element.click(); });
  await settle();
}

async function answer(value: boolean) {
  await act(async () => { respondToConfirmDialog(value); completeConfirmDialogClose(); });
  await settle();
}

function asking() {
  const state = readConfirmDialogState();
  return state.status === "confirming" ? state : null;
}

describe("Connections settings confirmations", () => {
  beforeEach(() => {
    queries = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    unregister = registerConfirmDialogHost();
    detection = { enabled: {}, autodelist: false, auto_adjust_quantity: false };
    vi.mocked(api.settings.saleDetection).mockImplementation(async () => detection as never);
    vi.mocked(api.settings.setSaleAutodelist).mockImplementation(async (value: boolean) => (detection = { ...detection, autodelist: value }) as never);
    vi.mocked(api.settings.setSaleAutoAdjustQuantity).mockImplementation(async (value: boolean) => (detection = { ...detection, auto_adjust_quantity: value }) as never);
    vi.mocked(api.directConnections.status).mockResolvedValue({ accounts: { ebay: { connected: true, label: "seller" } } } as never);
    vi.mocked(api.directConnections.disconnect).mockResolvedValue(undefined as never);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    unregister();
    queries.clear();
    container.remove();
    vi.mocked(api.settings.setSaleAutodelist).mockClear();
    vi.mocked(api.settings.setSaleAutoAdjustQuantity).mockClear();
    vi.mocked(api.directConnections.disconnect).mockClear();
  });

  it("asks before turning on Delist the other marketplaces, and keeps it off when cancelled", async () => {
    await render();
    await click(switchNamed("Delist the other marketplaces when something sells"));
    const prompt = asking();
    expect(prompt?.message).toContain("without asking each time");
    expect(prompt?.message).toContain("never the marketplace where it sold");
    expect(api.settings.setSaleAutodelist).not.toHaveBeenCalled();
    await answer(false);
    expect(api.settings.setSaleAutodelist).not.toHaveBeenCalled();
    expect(switchNamed("Delist the other marketplaces when something sells").getAttribute("aria-checked")).toBe("false");

    await click(switchNamed("Delist the other marketplaces when something sells"));
    await answer(true);
    expect(api.settings.setSaleAutodelist).toHaveBeenCalledWith(true);
    expect(switchNamed("Delist the other marketplaces when something sells").getAttribute("aria-checked")).toBe("true");
  });

  it("turns Delist the other marketplaces off without asking", async () => {
    detection.autodelist = true;
    await render(true);
    await click(switchNamed("Delist the other marketplaces when something sells"));
    expect(asking()).toBeNull();
    expect(api.settings.setSaleAutodelist).toHaveBeenCalledWith(false);
  });

  it("asks before turning on Auto-adjust quantity, and keeps it off when cancelled", async () => {
    await render();
    await click(switchNamed("Auto-adjust quantity across marketplaces when stock remains"));
    expect(asking()?.message).toContain("without asking each time");
    await answer(false);
    expect(api.settings.setSaleAutoAdjustQuantity).not.toHaveBeenCalled();

  });

  it("turns Auto-adjust quantity off without asking", async () => {
    detection.auto_adjust_quantity = true;
    await render();
    await click(switchNamed("Auto-adjust quantity across marketplaces when stock remains"));
    expect(asking()).toBeNull();
    expect(api.settings.setSaleAutoAdjustQuantity).toHaveBeenCalledWith(false);
  });

  it("asks before disconnecting a marketplace account and says what stops working", async () => {
    await render();
    await click(button("Disconnect"));
    const prompt = asking();
    expect(prompt?.message).toMatch(/^Disconnect eBay\?/);
    expect(prompt?.message).toContain("stops");
    expect(prompt?.variant).toBe("destructive");
    await answer(false);
    expect(api.directConnections.disconnect).not.toHaveBeenCalled();
    await click(button("Disconnect"));
    await answer(true);
    expect(api.directConnections.disconnect).toHaveBeenCalledWith("ebay");
  });

  it("keeps the post-sale settings in their own After a sale section", async () => {
    await render();
    const section = container.querySelector("section#after-sale");
    expect(section?.querySelector("h2")?.textContent).toBe("After a sale");
    expect(section?.querySelector("#sale-detection")).not.toBeNull();
    expect(section?.querySelector("#sale-autodelist")).not.toBeNull();
    expect(section?.querySelector("#sale-auto-adjust-quantity")).not.toBeNull();
    expect(container.querySelector("#accounts #sale-autodelist")).toBeNull();
  });

  it("keeps developer-only rows behind an Advanced disclosure", async () => {
    await render();
    const advanced = container.querySelector("details#advanced");
    expect(advanced?.querySelector("summary")?.textContent).toContain("Advanced");
    expect(advanced?.hasAttribute("open")).toBe(false);
    expect(advanced?.querySelector("#tailscale-https")).not.toBeNull();
    expect(advanced?.querySelector("#extension-folder")).not.toBeNull();
    expect(container.textContent).not.toContain("tailnet");
    expect(container.textContent).not.toContain("Funnel");
  });

  it("opens Advanced when Settings search jumps to a row inside it", async () => {
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 0; });
    window.matchMedia ??= (() => ({ matches: true })) as never;
    Element.prototype.scrollIntoView ??= () => {};
    const { SettingsPage } = await import("../SettingsPage");
    await act(async () => {
      root.render(<QueryClientProvider client={queries}>
        <SettingsPage section="connections" targetId="tailscale-https" onOpenListing={() => {}} />
      </QueryClientProvider>);
    });
    await settle();
    expect(container.querySelector("details#advanced")?.hasAttribute("open")).toBe(true);
    vi.unstubAllGlobals();
  }, 30_000);
});
