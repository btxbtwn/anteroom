/** T3 Code-style settings categories for Vendoo Studio. */

export type SettingsSectionId = "general" | "listings" | "assistant" | "providers" | "connections" | "agents" | "data" | "logs" | "help";

export type SettingsNavItem = {
  id: SettingsSectionId;
  label: string;
};

export type SettingsSearchItem = {
  id: string;
  title: string;
  section: SettingsSectionId;
  /** Optional row/section id to scroll to after navigation. */
  targetId?: string;
  searchTerms?: string[];
};

/** Sidebar order follows T3 Code: General first, then what the seller changes most. */
export const SETTINGS_NAV_ITEMS: readonly SettingsNavItem[] = [
  { id: "general", label: "General" },
  { id: "listings", label: "Listings" },
  { id: "assistant", label: "Assistant" },
  { id: "providers", label: "Providers" },
  { id: "connections", label: "Connections" },
  { id: "agents", label: "Agents" },
  { id: "data", label: "Data" },
  { id: "logs", label: "Logs" },
  { id: "help", label: "Help" },
];

export const SETTINGS_SECTION_LABELS: Readonly<Record<SettingsSectionId, string>> = {
  general: "General",
  listings: "Listings",
  assistant: "Assistant",
  providers: "Providers",
  connections: "Connections",
  agents: "Agents",
  data: "Data",
  logs: "Logs",
  help: "Help",
};

export const DEFAULT_SETTINGS_SECTION: SettingsSectionId = "general";

export const SETTINGS_SEARCH_ITEMS: readonly SettingsSearchItem[] = [
  {id:"listing-templates",title:"Saved templates",section:"listings",targetId:"listing-templates",searchTerms:["template","preset","reusable fields","saved defaults"]},
  {
    id: "business-note",
    title: "About your business",
    section: "assistant",
    targetId: "business-note",
    searchTerms: ["assistant", "business", "shop", "goals", "margin", "profit", "note", "keep in mind"],
  },
  {
    id: "assistant-memory",
    title: "What the assistant remembers",
    section: "assistant",
    targetId: "assistant-memory",
    searchTerms: ["assistant", "memory", "memories", "facts", "forget", "remove"],
  },
  {
    id: "duplicate-vendoo-links",
    title: "Duplicate check",
    section: "listings",
    targetId: "duplicate-vendoo-links",
    searchTerms: ["duplicate", "vendoo", "links", "same item"],
  },
  {
    id: "similar-items",
    title: "Similar items",
    section: "listings",
    targetId: "similar-items",
    searchTerms: ["duplicate finder", "duplicates", "similar", "merge", "same item", "listed twice"],
  },
  {
    id: "appearance",
    title: "Theme",
    section: "general",
    targetId: "appearance",
    searchTerms: ["appearance", "light mode", "dark mode", "system", "color scheme"],
  },
  {
    id: "listing-names",
    title: "Listing names",
    section: "general",
    targetId: "listing-names",
    searchTerms: ["sku", "sidebar", "rename", "listing title", "name by sku"],
  },
  {
    id: "vendoo",
    title: "Send to Vendoo",
    section: "listings",
    targetId: "vendoo",
    searchTerms: ["vendoo", "send to vendoo", "hide vendoo", "turn off vendoo", "disable vendoo"],
  },
  {
    id: "where-you-list",
    title: "Where you list",
    section: "listings",
    targetId: "where-you-list",
    searchTerms: ["marketplaces", "list to", "ebay", "poshmark", "mercari", "depop", "etsy", "vinted", "direct", "crosslist"],
  },
  {
    id: "package-dimensions",
    title: "Package dimensions",
    section: "listings",
    targetId: "package-dimensions",
    searchTerms: ["shipping", "box", "length", "width", "height", "size"],
  },
  {
    id: "ships-from-zip",
    title: "Ships from ZIP",
    section: "listings",
    targetId: "package-dimensions",
    searchTerms: ["zip", "zip code", "ships from", "shipping", "mercari", "depop"],
  },
  {
    id: "hidden-fields",
    title: "Always hidden fields",
    section: "listings",
    targetId: "hidden-fields",
    searchTerms: ["exclude", "hide fields"],
  },
  {
    id: "listing-formulas",
    title: "Title and description formulas",
    section: "listings",
    targetId: "listing-formulas",
    searchTerms: ["formula", "title", "description", "template", "flaws", "measurements"],
  },
  {
    id: "setup-guide",
    title: "Setup guide",
    section: "general",
    targetId: "setup-guide",
    searchTerms: ["tutorial", "onboarding", "first run", "chatgpt", "claude", "mimo", "cursor", "brave"],
  },
  {
    id: "vendoo-sync",
    title: "Sync from Vendoo",
    section: "data",
    targetId: "vendoo-sync",
    searchTerms: ["vendoo", "sync", "import", "inventory", "resync", "pull", "download"],
  },
  {
    id: "backups",
    title: "Backups",
    section: "data",
    targetId: "backups",
    searchTerms: ["backup", "snapshot", "restore", "lose data", "external drive", "copy", "sqlite"],
  },
  {
    id: "data-folder",
    title: "Data folder",
    section: "data",
    targetId: "data-folder",
    searchTerms: ["backup", "reinstall", "photos", "sqlite", "application support", "preserve"],
  },
  {
    id: "database-maintenance",
    title: "Database",
    section: "data",
    targetId: "database-maintenance",
    searchTerms: ["vacuum", "prune", "job events", "disk space", "table size", "bloat"],
  },
  {
    id: "about-version",
    title: "Version",
    section: "general",
    targetId: "about",
    searchTerms: ["about", "update", "check for updates"],
  },
  {
    id: "whats-new",
    title: "What's new",
    section: "general",
    targetId: "about",
    searchTerms: ["changelog", "release notes", "history", "changes", "what changed"],
  },
  {
    id: "previous-versions",
    title: "Previous versions",
    section: "general",
    targetId: "about",
    searchTerms: ["rollback", "roll back", "downgrade", "older version", "revert", "broken update"],
  },
  {
    id: "listing-ai",
    title: "Listing AI",
    section: "providers",
    targetId: "listing-ai",
    searchTerms: ["chatgpt", "claude", "mimo", "cursor", "provider", "choose", "prefer", "primary", "fallback", "photo analysis", "vision"],
  },
  {
    id: "chatgpt",
    title: "Sign in with ChatGPT",
    section: "providers",
    targetId: "chatgpt",
    searchTerms: ["openai", "codex", "subscription", "sign out", "model", "photo model", "reasoning"],
  },
  {
    id: "claude",
    title: "Sign in with Claude",
    section: "providers",
    targetId: "claude",
    searchTerms: ["anthropic", "claude code", "subscription", "pro", "max", "model", "sign out", "effort"],
  },
  {
    id: "mimo",
    title: "Xiaomi MiMo",
    section: "providers",
    targetId: "mimo",
    searchTerms: ["api key", "fallback", "xiaomi"],
  },
  {
    id: "cursor",
    title: "Cursor",
    section: "providers",
    targetId: "cursor",
    searchTerms: ["cursor", "composer", "api key", "sdk", "model", "reasoning"],
  },
  {
    id: "brave",
    title: "Brave Search",
    section: "providers",
    targetId: "brave",
    searchTerms: ["comps", "sold", "api key", "fallback search"],
  },
  {
    id: "chrome",
    title: "Vendoo in Chrome",
    section: "connections",
    targetId: "connections",
    searchTerms: ["extension", "connect chrome", "browser"],
  },
  {
    id: "extension-folder",
    title: "Listing extension folder",
    section: "connections",
    targetId: "extension-folder",
    searchTerms: ["load unpacked", "chrome extensions", "developer mode"],
  },
  {
    id: "category-trees",
    title: "Marketplace category trees",
    section: "connections",
    targetId: "category-trees",
    searchTerms: ["categories", "extract", "sync", "taxonomy"],
  },
  {
    id: "accounts",
    title: "Marketplace accounts",
    section: "connections",
    targetId: "accounts",
    searchTerms: ["marketplaces", "accounts", "depop", "poshmark", "mercari", "sellwild", "vinted", "ebay", "etsy", "detect sales", "sale detection", "cookie", "sign in"],
  },
  {
    id: "direct-ebay",
    title: "eBay",
    section: "connections",
    targetId: "direct-ebay",
    searchTerms: ["ebay", "sold", "sale", "detect sales", "delist", "connect", "sign in", "notification"],
  },
  {
    id: "ebay-inventory-location",
    title: "eBay inventory location",
    section: "connections",
    targetId: "ebay-inventory-location",
    searchTerms: ["inventory location", "zip", "zip code", "shipping location", "item location", "ebay", "ship from"],
  },
  {
    id: "direct-etsy",
    title: "Etsy",
    section: "connections",
    targetId: "direct-etsy",
    searchTerms: ["etsy", "sold", "sale", "detect sales", "delist", "connect", "sign in", "notification"],
  },
  {
    id: "agent-cli",
    title: "Connect an agent",
    section: "agents",
    targetId: "agent-cli",
    searchTerms: ["anteroom", "cli", "command line", "mcp", "cursor", "claude desktop", "claude code", "agent"],
  },
  {
    id: "tailscale-https",
    title: "Tailscale HTTPS",
    section: "connections",
    targetId: "tailscale-https",
    searchTerms: ["remote access", "mobile", "phone", "remote", "tailnet", "serve", "network"],
  },
  {
    id: "error-logs",
    title: "Errors",
    section: "logs",
    targetId: "error-logs",
    searchTerms: ["error", "warning", "failed", "problem", "log", "debug", "crash"],
  },
  {
    id: "vendoo-api-logs",
    title: "Vendoo API logs",
    section: "logs",
    targetId: "vendoo-api-logs",
    searchTerms: ["api", "request", "http", "live", "log", "vendoo", "debug", "troubleshooting"],
  },
  {
    id: "help",
    title: "How-to guides",
    section: "help",
    targetId: "help",
    searchTerms: ["help", "how to", "guide", "steps", "instructions", "faq"],
  },
];

const VENDOO_ONLY_SEARCH_IDS = new Set([
  "duplicate-vendoo-links",
  "vendoo-api-logs",
  "category-trees",
  "vendoo-sync",
]);

/** Search rows that only exist while Send to Vendoo is on. The switch itself stays. */
export function settingsSearchItemsForMode(vendooOn: boolean): readonly SettingsSearchItem[] {
  if (vendooOn) return SETTINGS_SEARCH_ITEMS;
  return SETTINGS_SEARCH_ITEMS
    .filter((item) => !VENDOO_ONLY_SEARCH_IDS.has(item.id))
    .map((item) => (item.id === "chrome" ? { ...item, title: "Chrome" } : item));
}

function normalize(text: string): string {
  return text.trim().toLowerCase().replace(/\s+/g, " ");
}

export function searchSettings(query: string, items: readonly SettingsSearchItem[] = SETTINGS_SEARCH_ITEMS): SettingsSearchItem[] {
  const needle = normalize(query);
  if (!needle) return [];
  return items.filter((item) => {
    const haystack = [item.title, ...(item.searchTerms || [])].map(normalize).join(" ");
    return haystack.includes(needle) || needle.split(" ").every((part) => haystack.includes(part));
  });
}

export function isSettingsSectionId(value: string): value is SettingsSectionId {
  return SETTINGS_NAV_ITEMS.some((item) => item.id === value);
}
