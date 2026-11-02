import { createContext, useContext, type ReactNode } from "react";

export type WorkspaceFeature = "inventory" | "listing_fields" | "photos" | "preferences" | "generation" | "assistant" | "suggestions" | "marketplace_actions" | "marketplace_sessions" | "photo_edits" | "photo_background" | "chat_attachments" | "bulk_actions" | "bulk_edit" | "bulk_copy" | "bulk_delete" | "bulk_regenerate" | "bulk_markdowns" | "catalog_categories" | "ebay_offers";
export interface WorkspaceRuntime {
  hosted: boolean;
  features: ReadonlySet<WorkspaceFeature>;
  views: ReadonlySet<string>;
  marketplaceForms: ReadonlySet<string>;
  /** Marketplaces this app can list on, update and delist from. */
  actionMarketplaces: ReadonlySet<string>;
  accountControls?: ReactNode;
}
export const desktopRuntime: WorkspaceRuntime = {
  hosted: false,
  features: new Set(["inventory", "listing_fields", "photos", "preferences", "generation", "assistant", "suggestions", "marketplace_actions", "marketplace_sessions", "photo_edits", "photo_background", "chat_attachments", "bulk_actions", "bulk_edit", "bulk_copy", "bulk_delete", "bulk_regenerate", "bulk_markdowns", "catalog_categories", "ebay_offers"]),
  views: new Set(["listings", "assistant", "settings", "analytics", "sourcing", "marketplaces", "automations"]),
  marketplaceForms: new Set(["ebay", "poshmark", "mercari", "depop", "etsy", "vinted", "sellwild"]),
  actionMarketplaces: new Set(["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild", "vinted"]),


};
const RuntimeContext = createContext<WorkspaceRuntime>(desktopRuntime);
export const WorkspaceRuntimeProvider = RuntimeContext.Provider;
export function useWorkspaceRuntime() { return useContext(RuntimeContext); }
