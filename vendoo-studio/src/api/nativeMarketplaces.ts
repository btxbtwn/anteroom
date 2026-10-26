export type NativeMarketplace = "ebay" | "poshmark" | "mercari" | "depop" | "etsy" | "sellwild" | "vinted";
/** What the marketplace's refusal was, so the section can offer the one action that fixes it. */
export type NativeErrorCode = "signed_out" | "challenge" | "rate_limited" | "missing_fields" | "rejected" | "schema_drift" | "network" | "uncertain";
export interface NativeErrorField { field: string; message: string }
/** One item's direct listing on one marketplace, as Anteroom recorded it. */
export interface NativeDraft {
  state: "not_saved" | "saving" | "publishing" | "updating" | "unknown" | "draft" | "published" | "pending_review";
  action?: "save_draft" | "publish" | "list" | "update";
  storage?: "local";
  sellwild_category_id?: number;
  sellwild_condition_id?: number;
  draft_url?: string;
  listing_url?: string;
  revision_id?: string | null;
  job_id?: string;
  error?: string | null;
  error_code?: NativeErrorCode | null;
  error_fields?: NativeErrorField[] | null;
  /** The marketplace's own words, shown beside Anteroom's. */
  marketplace_message?: string | null;
  /** ISO time before which a rate-limited List should wait. */
  retry_after?: string | null;
  /** True while a List, Update or Check job is running in this Studio. */
  running?: boolean;
  progress?: { step: string; label: string; index: number; total: number } | null;
}
export type NativeDrafts = Partial<Record<NativeMarketplace, NativeDraft>>;
export interface MarketplaceInventoryObservation {
  listing_id: string;
  state: string;
  price: number | null;
  quantity: number | null;
  warning: string | null;
  differences: { field: "price" | "quantity"; local: number | null; remote: number }[];
}
/** What Sync checked on each marketplace with a direct listing. */
export interface NativeSync {
  checked_at: string;
  marketplaces: Partial<Record<NativeMarketplace, { ok: boolean; error: string | null; inventory?: MarketplaceInventoryObservation }>>;
}
export interface PreflightGap { field: string; message: string; blocking: boolean }
export interface NativePreflight {
  revision_id: string | null;
  marketplaces: Partial<Record<NativeMarketplace, { ready: boolean; gaps: PreflightGap[] }>>;
}
/** The seller's own Sellwild picks. A value left out is matched from the listing on every List. */
export interface SellwildChoice { categoryId?: number; conditionId?: number }
export interface SellwildOptions {
  categories: { id: number; label: string }[];
  conditions: { id: number; label: string }[];
  suggested: { category_id: number | null; condition_id: number | null };
}
