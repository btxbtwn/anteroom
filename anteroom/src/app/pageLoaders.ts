import { createElement, lazy, useState, type ComponentType } from "react";

export const loadListingEditor = () => import("../components/ListingEditor").then(m => ({ default: m.ListingEditor }));
export const loadChatPanel = () => import("../components/ChatPanel").then(m => ({ default: m.ChatPanel }));

type PageModule<P> = { default: ComponentType<P> };

/**
 * A page whose code loads on demand. `React.lazy` waits a turn and shows its
 * fallback the first time it renders even when the code is already here; once
 * `preload` has finished, the page renders straight away instead.
 */
export function preloadablePage<P extends object>(load: () => Promise<PageModule<P>>) {
  let loaded: ComponentType<P> | undefined;
  let pending: Promise<PageModule<P>> | undefined;
  const preload = () => pending ??= load().then(
    (module) => { loaded = module.default; return module; },
    (error: unknown) => { pending = undefined; throw error; },
  );
  const Lazy = lazy(preload) as unknown as ComponentType<P>;
  function Page(props: P) {
    // Chosen once per visit, so a page never remounts when its code arrives.
    const [Component] = useState(() => loaded ?? Lazy);
    return createElement(Component, props);
  }
  return { Page, preload };
}

const settings = preloadablePage(() => import("../components/SettingsPage").then(m => ({ default: m.SettingsPage })));
const analytics = preloadablePage(() => import("../components/AnalyticsPage").then(m => ({ default: m.AnalyticsPage })));
const assistant = preloadablePage(() => import("../components/AssistantPage").then(m => ({ default: m.AssistantPage })));
const marketplaces = preloadablePage(() => import("../components/MarketplacesPage").then(m => ({ default: m.MarketplacesPage })));
const automations = preloadablePage(() => import("../components/AutomationsPage").then(m => ({ default: m.AutomationsPage })));
const sourcing = preloadablePage(() => import("../components/SourcingPage").then(m => ({ default: m.SourcingPage })));
const inventory = preloadablePage(() => import("../components/InventoryTablePage").then(m => ({ default: m.InventoryTablePage })));

export const pageLoaders = {
  settings: settings.preload,
  analytics: analytics.preload,
  assistant: assistant.preload,
  marketplaces: marketplaces.preload,
  automations: automations.preload,
  sourcing: sourcing.preload,
  inventory: inventory.preload,
};

export const SettingsPage = settings.Page;
export const AnalyticsPage = analytics.Page;
export const AssistantPage = assistant.Page;
export const MarketplacesPage = marketplaces.Page;
export const AutomationsPage = automations.Page;
export const SourcingPage = sourcing.Page;
export const InventoryTablePage = inventory.Page;

export function preloadListingCode() {
  void Promise.all([loadListingEditor(), loadChatPanel()]).catch(() => {});
}
