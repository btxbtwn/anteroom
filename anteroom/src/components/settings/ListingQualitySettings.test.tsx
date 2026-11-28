// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { WorkspaceRuntimeProvider, type WorkspaceRuntime } from "../../app/runtime";
import { ListingsSettings } from "./ListingsSettings";
import { ListingQualitySettings, listingQualityConsentKey } from "./ListingQualitySettings";

const { readConsent, saveConsent } = vi.hoisted(() => ({ readConsent: vi.fn(), saveConsent: vi.fn() }));
vi.mock("../../api/client", async (original) => {
  const actual = await original<typeof import("../../api/client")>();
  return { ...actual, api: { ...actual.api, settings: { ...actual.api.settings,
    listingQualityConsent: readConsent, setListingQualityConsent: saveConsent } } };
});
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const runtime = (hosted: boolean): WorkspaceRuntime => ({
  hosted, features: new Set(), views: new Set(["settings"]),
  marketplaceForms: new Set<string>(), actionMarketplaces: new Set<string>(),
});

let root: Root;
let client: QueryClient;
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  document.body.innerHTML = "";
  vi.clearAllMocks();
});

const toggle = () => document.querySelector<HTMLElement>('[role="switch"][aria-label="Help improve listings"]')!;
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });

async function render() {
  await act(async () => root.render(
    <WorkspaceRuntimeProvider value={runtime(true)}>
      <QueryClientProvider client={client}><ListingQualitySettings /></QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  ));
  await settle();
}

it("shows sharing off and locked until the seller's choice loads", async () => {
  readConsent.mockReturnValue(new Promise(() => {}));
  await render();
  expect(toggle().getAttribute("aria-checked")).toBe("false");
  expect(toggle().hasAttribute("data-disabled")).toBe(true);
  expect(document.body.textContent).toContain("Off unless you turn it on. Turning it off deletes the counts recorded so far.");
  expect(document.body.textContent).toContain("Your photos, listing text, chats and field values stay out of these reports.");
  expect(document.querySelector('a[href="/privacy#listing-quality"]')).not.toBeNull();
});

it("turns sharing on only when the seller flips the switch", async () => {
  readConsent.mockResolvedValue({ enabled: false });
  saveConsent.mockResolvedValue({ enabled: true });
  await render();
  expect(saveConsent).not.toHaveBeenCalled();
  expect(toggle().getAttribute("aria-checked")).toBe("false");
  await act(async () => toggle().click());
  await settle();
  expect(saveConsent).toHaveBeenCalledTimes(1);
  expect(saveConsent.mock.calls[0][0]).toBe(true);
  expect(toggle().getAttribute("aria-checked")).toBe("true");
  expect(client.getQueryData(listingQualityConsentKey(undefined))).toEqual({ enabled: true });
});

it("puts the switch back and says so when saving fails", async () => {
  readConsent.mockResolvedValue({ enabled: true });
  saveConsent.mockRejectedValue(new Error("Server unavailable"));
  await render();
  await act(async () => toggle().click());
  await settle();
  expect(toggle().getAttribute("aria-checked")).toBe("true");
  expect(document.querySelector('[role="alert"]')?.textContent).toContain("Couldn’t save this setting. Server unavailable");
});

it("says when the choice can't be read and leaves sharing off", async () => {
  readConsent.mockRejectedValue(new Error("Request failed: 503"));
  await render();
  expect(toggle().getAttribute("aria-checked")).toBe("false");
  expect(toggle().hasAttribute("data-disabled")).toBe(true);
  expect(document.querySelector('[role="alert"]')?.textContent).toContain("Couldn’t load this setting.");
});

it("appears in Listings on the web, not on the Mac", () => {
  const html = (hosted: boolean) => {
    const cache = new QueryClient();
    cache.setQueryData(listingQualityConsentKey(undefined), { enabled: false });
    return renderToStaticMarkup(
      <WorkspaceRuntimeProvider value={runtime(hosted)}>
        <QueryClientProvider client={cache}><ListingsSettings onOpenListing={() => {}} /></QueryClientProvider>
      </WorkspaceRuntimeProvider>,
    );
  };
  expect(html(true)).toContain("Help improve listings");
  expect(html(false)).not.toContain("Help improve listings");
});
