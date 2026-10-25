export type NativeMarketplace = "poshmark" | "mercari" | "depop" | "etsy" | "sellwild";
export interface EtsyDraftOptions {
  shippingProfileId?: string;
  readinessStateId?: string;
  package?: { item_weight: number; item_length: number; item_width: number; item_height: number;
    item_weight_unit: "oz"; item_dimensions_unit: "in" };
}
export interface NativeDraft {
  state: "not_saved" | "saving" | "publishing" | "unknown" | "draft" | "published" | "pending_review";
  storage?: "local";
  sellwild_category_id?: number;
  sellwild_condition_id?: number;
  draft_url?: string;
  listing_url?: string;
  revision_id: string | null;
  job_id?: string;
  error?: string | null;
}
export type NativeDrafts = Partial<Record<NativeMarketplace, NativeDraft>>;
export interface SellwildChoice { categoryId: number; conditionId: number }
export interface SellwildOptions {
  categories: { id: number; label: string }[];
  conditions: { id: number; label: string }[];
  suggested: { category_id: number | null; condition_id: number | null };
}
