export type NativeMarketplace = "ebay" | "poshmark" | "mercari" | "depop" | "etsy";
/** What the marketplace's refusal was, so the section can offer the one action that fixes it. */
export type NativeErrorCode = "signed_out" | "challenge" | "rate_limited" | "missing_fields" | "rejected" | "schema_drift" | "network" | "uncertain";
export interface NativeErrorField { field: string; message: string }
/** One item's direct listing on one marketplace, as Anteroom recorded it. */
export interface NativeDraft {
  state: "not_saved" | "saving" | "publishing" | "updating" | "unknown" | "draft" | "published" | "pending_review";
  action?: "save_draft" | "publish" | "list" | "update";
  storage?: "local";
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
export interface PreflightGap { field: string; message: string; blocking: boolean; required?: boolean; label?: string }
/** A field a marketplace still needs: required and empty, or rejected on the last List. Counted once. */
export interface MissingField { field: string; label: string; message: string }
export interface PreflightCheck { ready: boolean; gaps: PreflightGap[]; missing: MissingField[] }
export interface NativePreflight {
  revision_id: string | null;
  marketplaces: Partial<Record<NativeMarketplace, PreflightCheck>>;
}
/** Each listing's missing fields per marketplace (`GET /listing-readiness`), for the inventory. */
export interface ListingReadiness {
  revision_id: string | null;
  marketplaces: Partial<Record<NativeMarketplace, { missing: MissingField[] }>>;
}
