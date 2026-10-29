export const loadListingEditor = () => import("../components/ListingEditor").then(m => ({ default: m.ListingEditor }));
export const loadChatPanel = () => import("../components/ChatPanel").then(m => ({ default: m.ChatPanel }));

export const pageLoaders = {
  settings: () => import("../components/SettingsPage").then(m => ({ default: m.SettingsPage })),
  analytics: () => import("../components/AnalyticsPage").then(m => ({ default: m.AnalyticsPage })),
  assistant: () => import("../components/AssistantPage").then(m => ({ default: m.AssistantPage })),
  marketplaces: () => import("../components/MarketplacesPage").then(m => ({ default: m.MarketplacesPage })),
  usage: () => import("../components/UsagePage").then(m => ({ default: m.UsagePage })),
  automations: () => import("../components/AutomationsPage").then(m => ({ default: m.AutomationsPage })),
  sourcing: () => import("../components/SourcingPage").then(m => ({ default: m.SourcingPage })),
};

export function preloadListingCode() {
  void Promise.all([loadListingEditor(), loadChatPanel()]).catch(() => {});
}
