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

function render(activeView: WorkspaceView = "listings", onOpenHome = noop) {
  act(() => {
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(), views: new Set(["home", "assistant", "sourcing", "analytics", "marketplaces", "automations", "settings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
          <NavRail activeView={activeView} onOpenHome={onOpenHome} onOpenSourcing={noop} onOpenAnalytics={noop}
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

it("names every page in a labelled navigation and marks the open one", () => {
  render("marketplaces");
  const nav = container.querySelector('nav[aria-label="Pages"]')!;
  for (const label of ["Today", "Assistant", "Sourcing", "Analytics", "Marketplaces", "Automations", "Settings"]) {
    expect(nav.querySelector(`button[aria-label="${label}"]`)?.textContent).toContain(label);
  }
  expect(nav.querySelector('[aria-current="page"]')?.getAttribute("aria-label")).toBe("Marketplaces");
  render("settings");
  expect(nav.querySelector('[aria-current="page"]')?.getAttribute("aria-label")).toBe("Settings");
});

it("opens a page from the rail", () => {
  const onOpenHome = vi.fn();
  render("listings", onOpenHome);
  act(() => container.querySelector<HTMLButtonElement>('button[aria-label="Today"]')!.click());
  expect(onOpenHome).toHaveBeenCalledOnce();
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
