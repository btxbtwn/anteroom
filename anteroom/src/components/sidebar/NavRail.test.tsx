// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { WorkspaceRuntimeProvider } from "../../app/runtime";
import type { WorkspaceView } from "../workspaceCrumbs";
import { NAV_RAIL_CLOSE_DELAY_MS, NAV_RAIL_OPEN_DELAY_MS, NavRail } from "./NavRail";

const noop = () => {};
let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.useFakeTimers();
  localStorage.clear();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

function render(
  activeView: WorkspaceView = "listings",
  options: {
    hosted?: boolean;
    onOpenHome?: () => void;
    onOpenInventory?: () => void;
    updateStatus?: { available: boolean; summary?: string };
  } = {},
) {
  const { hosted = true, onOpenHome = noop, onOpenInventory = noop, updateStatus } = options;
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, refetchOnMount: false, refetchOnWindowFocus: false },
    },
  });
  if (!hosted) {
    queryClient.setQueryData(["updates"], updateStatus ?? { available: false });
    queryClient.setQueryData(["update-progress"], { status: "idle", download_percent: null, sha: null, error: null });
  }
  act(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <WorkspaceRuntimeProvider value={{ hosted, features: new Set(), views: new Set(["home", "assistant", "listings", "analytics", "marketplaces", "automations", "settings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
          <NavRail activeView={activeView} onOpenHome={onOpenHome} onOpenInventory={onOpenInventory} onOpenAnalytics={noop}
            onOpenMarketplaces={noop} onOpenAutomations={noop} onOpenAssistant={noop} onOpenSettings={noop} />
        </WorkspaceRuntimeProvider>
      </QueryClientProvider>,
    );
  });
}

const rail = () => container.querySelector<HTMLElement>(".nav-rail")!;
const panel = () => container.querySelector<HTMLElement>(".nav-rail-panel")!;
const expanded = () => rail().dataset.expanded === "true";
const pointer = (type: "pointerover" | "pointerout", target: Element, relatedTarget: Element | null) =>
  act(() => { target.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerType: "mouse", relatedTarget })); });
const press = (key: string, target: EventTarget = document.body) =>
  act(() => { target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true })); });

it("names every page in the same order as phones and marks the open one", () => {
  render("marketplaces");
  const nav = container.querySelector('nav[aria-label="Pages"]')!;
  const labels = Array.from(nav.querySelectorAll("button")).map((button) => button.getAttribute("aria-label"));
  expect(labels).toEqual(["Today", "Assistant", "Inventory", "Analytics", "Marketplaces", "Automations", "Settings"]);
  for (const label of labels) {
    expect(nav.querySelector(`button[aria-label="${label}"]`)?.textContent).toContain(label);
  }
  expect(nav.querySelector('[aria-current="page"]')?.getAttribute("aria-label")).toBe("Marketplaces");
  render("inventory");
  expect(nav.querySelector('[aria-current="page"]')?.getAttribute("aria-label")).toBe("Inventory");
  render("listings");
  expect(nav.querySelector('[aria-current="page"]')?.getAttribute("aria-label")).toBe("Inventory");
  render("settings");
  expect(nav.querySelector('[aria-current="page"]')?.getAttribute("aria-label")).toBe("Settings");
});

it("opens a page or inventory from the rail", () => {
  const onOpenHome = vi.fn();
  const onOpenInventory = vi.fn();
  render("home", { onOpenHome, onOpenInventory });
  act(() => container.querySelector<HTMLButtonElement>('button[aria-label="Today"]')!.click());
  expect(onOpenHome).toHaveBeenCalledOnce();
  act(() => container.querySelector<HTMLButtonElement>('button[aria-label="Inventory"]')!.click());
  expect(onOpenInventory).toHaveBeenCalledOnce();
});

it("gives the update control a visible label when the rail opens", () => {
  render("home", { hosted: false });
  const idleButton = panel().querySelector<HTMLButtonElement>('button[aria-label="Check for updates"]');
  expect(idleButton).toBeTruthy();
  expect(idleButton!.textContent?.trim()).toBe("Check for updates");

  act(() => root.unmount());
  root = createRoot(container);
  render("home", { hosted: false, updateStatus: { available: true, summary: "New build ready" } });
  const availableButton = panel().querySelector<HTMLButtonElement>('button[aria-label="Download update"]');
  expect(availableButton).toBeTruthy();
  expect(availableButton!.textContent?.trim()).toBe("Download update");
});

it("widens after a mouse rests on it and narrows a moment after it leaves", () => {
  render();
  pointer("pointerover", panel(), document.body);
  act(() => vi.advanceTimersByTime(NAV_RAIL_OPEN_DELAY_MS - 1));
  expect(expanded()).toBe(false);
  act(() => vi.advanceTimersByTime(1));
  expect(expanded()).toBe(true);

  pointer("pointerout", panel(), document.body);
  act(() => vi.advanceTimersByTime(NAV_RAIL_CLOSE_DELAY_MS - 1));
  expect(expanded()).toBe(true);
  act(() => vi.advanceTimersByTime(1));
  expect(expanded()).toBe(false);
});

it("ignores a mouse that only passes over it", () => {
  render();
  pointer("pointerover", panel(), document.body);
  act(() => vi.advanceTimersByTime(NAV_RAIL_OPEN_DELAY_MS / 2));
  pointer("pointerout", panel(), document.body);
  act(() => vi.advanceTimersByTime(NAV_RAIL_OPEN_DELAY_MS * 4));
  expect(expanded()).toBe(false);
});

it("Escape closes the widened rail", () => {
  render();
  pointer("pointerover", panel(), document.body);
  act(() => vi.advanceTimersByTime(NAV_RAIL_OPEN_DELAY_MS));
  press("Escape", panel());
  expect(expanded()).toBe(false);
});

it("[ keeps it open across reloads, except while typing", () => {
  render();
  press("[");
  expect(rail().dataset.pinned).toBe("true");
  expect(expanded()).toBe(true);

  act(() => root.unmount());
  root = createRoot(container);
  render();
  expect(rail().dataset.pinned).toBe("true");

  const input = document.createElement("input");
  document.body.append(input);
  press("[", input);
  expect(rail().dataset.pinned).toBe("true");
  input.remove();

  act(() => container.querySelector<HTMLButtonElement>('button[aria-pressed="true"]')!.click());
  expect(rail().dataset.pinned).toBe("false");
  expect(expanded()).toBe(false);
});
