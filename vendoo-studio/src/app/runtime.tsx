import { createContext, useContext, type ReactNode } from "react";

export type WorkspaceFeature = "inventory" | "listing_fields" | "photos" | "preferences" | "generation" | "assistant" | "suggestions" | "vendoo" | "marketplace_actions" | "marketplace_sessions" | "photo_edits" | "photo_background" | "chat_attachments" | "bulk_actions" | "bulk_edit" | "bulk_copy" | "bulk_delete" | "bulk_regenerate" | "bulk_markdowns" | "catalog_categories";
export interface WorkspaceRuntime {
  hosted: boolean;
  features: ReadonlySet<WorkspaceFeature>;
  views: ReadonlySet<string>;
  marketplaceForms: ReadonlySet<string>;
  accountControls?: ReactNode;
}
const desktop: WorkspaceRuntime = {
  hosted: false,
  features: new Set(["inventory", "listing_fields", "photos", "preferences", "generation", "assistant", "suggestions", "vendoo", "marketplace_actions", "marketplace_sessions", "photo_edits", "photo_background", "chat_attachments", "bulk_actions", "bulk_edit", "bulk_copy", "bulk_delete", "bulk_regenerate", "bulk_markdowns", "catalog_categories"]),
  views: new Set(["listings", "assistant", "settings", "analytics", "sourcing", "marketplaces", "automations", "usage"]),
  marketplaceForms: new Set(["ebay", "poshmark", "mercari", "depop", "etsy", "vinted", "sellwild", "grailed", "facebook"]),
};
const RuntimeContext = createContext<WorkspaceRuntime>(desktop);
export const WorkspaceRuntimeProvider = RuntimeContext.Provider;
export function useWorkspaceRuntime() { return useContext(RuntimeContext); }
