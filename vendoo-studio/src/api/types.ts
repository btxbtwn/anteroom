/** Response shapes for the Studio FastAPI backend. Keep in sync with server/vendoo_studio/routes. */
import type { AnalyticsAds } from "./adSpend";
import type { LabelColor } from "../labelColors";

export type ListingData = Record<string, unknown>;

/** One inventory row. Notes stay on the full Conversation: the sidebar polls
 * every row, and only the open listing reads them. */
export interface ConversationSummary {
  id: string;
  title: string | null;
  status: string;
  /** The wholesale box this item came out of. */
  box_id?: string | null;
  settled_at?: string | null;
  unsettled_at?: string | null;
  created_at: string;
  updated_at: string;
  cover_photo_url?: string | null;
  vendoo_status?: string | null;
  vendoo_cover_url?: string | null;
  /** What the sidebar filters on: the listing's SKU and price, its Vendoo
   * labels, and the marketplaces it is live on. */
  sku?: string | null;
  price?: number | null;
  vendoo_labels?: string[];
  vendoo_marketplaces?: string[];
  /** Marketplaces Anteroom published directly. Vendoo may still call the item a draft. */
  direct_marketplaces?: string[];
  /** Marketplaces Anteroom delisted directly. Vendoo may still call the item listed there. */
  removed_marketplaces?: string[];
  /** Units sold on Anteroom's sales ledger, and the most the item was offered with. */
  units_sold?: number;
  quantity?: number | null;
  /** Each marketplace listing's own page, where Vendoo recorded one. */
  vendoo_listing_urls?: Record<string, string>;
  /** Vendoo's own time tracking: when the item was created, last modified,
   * last went live (a relist moves this) and sold, plus the listing and sale
   * date per marketplace. */
  vendoo_created_at?: string | null;
  vendoo_modified_at?: string | null;
  vendoo_listed_at?: string | null;
  vendoo_sold_at?: string | null;
  vendoo_listed_dates?: Record<string, string>;
  vendoo_sold_dates?: Record<string, string>;
  /** When Studio last wrote this listing onto its Vendoo item. A marketplace
   * listed before this still shows the copy from before that write. */
  vendoo_form_updated_at?: string | null;
  /** Marketplaces owed a relist since then, kept across the delist itself. */
  vendoo_relist_pending?: string[];
  /** Edited here since the listing was last sent: to its live direct
   * listings when it has any (Update them), otherwise to Vendoo. */
  unsent_edits?: boolean;
  /** False for modern resale: Etsy removes it, so Send leaves Etsy empty. */
  etsy_eligible?: boolean;
}

export interface Conversation extends ConversationSummary {
  notes: string | null;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: string;
  text: string;
  provider: string | null;
  model: string | null;
  created_at: string;
  /** Images the seller attached; served from /api/chat-attachments/{id}. */
  attachments?: ChatAttachment[];
}

/** An image attached to a chat message; the file lives in Studio's data folder. */
export interface ChatAttachment {
  id: string;
  name: string;
  mime_type: string;
  size_bytes?: number;
  width?: number | null;
  height?: number | null;
}

/** An image the composer is about to send, as a data URL. */
export interface ChatAttachmentUpload {
  name: string;
  data_url: string;
}

/** Everything still running for a listing; chat is only done when ``busy`` is false. */
export interface ConversationActivity {
  busy: boolean;
  items: string[];
  message_count: number;
  last_message_id: string | null;
}

export interface Photo {
  id: string;
  conversation_id: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  display_order: number;
  width: number | null;
  height: number | null;
  created_at: string;
  url: string;
  /** The edits served in place of the kept original; null when unedited. */
  edits?: PhotoEdits | null;
  /** Set for a photo added on one marketplace's form; only that marketplace can send it. */
  marketplace?: string | null;
}

/** A crop as fractions of the turned and straightened photo. */
export interface CropBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Non-destructive edits, rendered by the server from the original upload. */
export interface PhotoEdits {
  /** Quarter turns, clockwise. */
  rotate: 0 | 90 | 180 | 270;
  /** Degrees clockwise, -45 to 45. */
  straighten: number;
  crop: CropBox | null;
  /** Stops, -2 to 2. */
  exposure: number;
  /** -100 to 100. */
  brightness: number;
  /** -100 to 100. */
  contrast: number;
  /** Colour behind the cut-out item, as #rrggbb; null keeps the photo's own background. */
  background: string | null;
}

export interface PhotoUploadResult {
  ok: boolean;
  count: number;
  photos: Photo[];
  errors?: string[];
  message?: string;
}

export interface OkResponse {
  ok: boolean;
}

export interface DeleteConversationResult extends OkResponse {
  deleted_jobs: number;
  deleted_photos: number;
}

export type ValidationIssue = Record<string, string>;

export interface ListingResponse {
  conversation_id: string;
  current_revision_id: string | null;
  listing: ListingData;
  revision_count: number;
  can_send: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  /** False for modern resale: Etsy removes it, so Send leaves Etsy empty. */
  etsy_eligible?: boolean;
}

export interface ValidationResult {
  valid: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  info: ValidationIssue[];
  can_send: boolean;
}

export interface ListingUpdateResult extends OkResponse {
  revision_id: string;
  listing: ListingData;
  etsy_eligible: boolean;
  validation: ValidationResult;
}

export interface ListingRevision {
  id: string;
  source: string;
  created_at: string;
  parent_revision_id: string | null;
  title: string;
}

export interface RevisionRestoreResult extends OkResponse {
  revision_id: string;
}

export interface ListingChange {
  field: string;
  before: unknown;
  after: unknown;
}

export interface SendPreview {
  review_id: string;
  revision_id: string;
  mode: "create" | "update";
  changes: ListingChange[];
  photo_count: number;
  photo_action: "keep" | "upload";
  warnings: string[];
}

export interface PriceDropHistoryEvent {
  from_price: number;
  to_price: number;
  percent: number;
  created_at: string;
  revision_id: string;
}

export interface PriceDropComps {
  available: boolean;
  text: string;
  market: string;
  market_midpoint: number | null;
  target_price: number | null;
  source: string;
  query: string;
}

/** One comps source's progress: ChatGPT, Cursor, MiMo, or the Brave fallback. */
export interface PriceDropCompsSource {
  source: string;
  state: "searching" | "done" | "failed" | "timeout" | string;
  sold: number;
  live: number;
  detail: string;
}

export interface PriceDropOption {
  price: number;
  /** The target that produced this price; the label uses effective_percent. */
  percent: number;
  effective_percent: number;
}

export interface PriceDropSellThrough {
  /** "category" | "brand" | "all" — how the cohort was picked. */
  scope: string;
  label: string;
  count: number;
  /** null when too few of those sales show what the price moved off. */
  median_discount_percent: number | null;
  /** How many of the cohort's sales carried a usable discount. */
  discount_count: number;
  median_days: number | null;
}

export interface PriceDropPreview {
  current_price: number;
  sell_through: PriceDropSellThrough | null;
  age_days: number | null;
  first_price: number;
  drop_options: PriceDropOption[];
  suggested_percent: number;
  suggested_effective_percent: number;
  suggested_price: number;
  suggested_mode: "percent" | "comps" | "sell_through" | string;
  suggested_reason: string;
  percent_options: number[];
  prices_by_percent: Record<string, number>;
  comps: PriceDropComps;
  history: PriceDropHistoryEvent[];
}

export interface PriceDropApplyResult extends OkResponse {
  revision_id: string;
  price: number;
  previous_price: number | null;
  percent: number | null;
  mode: string;
  validation: ValidationResult;
}

/** One listing in a batch markdown review. ``skip_reason`` set means it cannot be dropped. */
export interface MarkdownPreviewRow {
  conversation_id: string;
  title: string;
  cover_photo_url: string | null;
  revision_id: string | null;
  current_price: number | null;
  suggested_price: number | null;
  suggested_effective_percent: number | null;
  suggested_mode: string | null;
  suggested_reason: string;
  age_days: number | null;
  skip_reason: string | null;
}

export interface MarkdownDropRequest {
  conversation_id: string;
  price: number;
  expected_revision_id?: string | null;
}

export interface MarkdownApplyResult {
  applied: {
    conversation_id: string;
    revision_id: string;
    price: number;
    previous_price: number | null;
    valid: boolean;
  }[];
  skipped: { conversation_id: string; reason: string }[];
}

export interface Job {
  id: string;
  conversation_id: string;
  status: string;
  current_step: string | null;
  vendoo_item_id: string | null;
  vendoo_url: string | null;
  attempt_count: number;
  last_error: string | null;
  listing_title: string;
  mode?: string | null;
  blocker_fields?: Record<string, unknown>[] | null;
  send_progress?: { completed: number; total: number } | null;
  started_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface VendooItemResult {
  ok: boolean;
  source?: string | null;
  item_id?: string | null;
  url?: string | null;
  error?: string | null;
  api_error?: string | null;
  item?: Record<string, unknown> | null;
  form?: Record<string, unknown> | null;
  statuses?: Record<string, unknown> | null;
}

/** A Sign in with ChatGPT waiting on the browser; the page can open it again. */
export interface ChatGPTPendingLogin {
  authorize_url: string;
}

export interface ChatGPTStatus {
  signed_in: boolean;
  email?: string | null;
  plan?: string | null;
  pending?: ChatGPTPendingLogin | null;
  error?: string | null;
}

export interface ClaudeStatus {
  /** Whether Claude Code, whose login Studio signs in with, is on this Mac. */
  installed: boolean;
  signed_in: boolean;
  email?: string | null;
  plan?: string | null;
  install_command: string;
}

/** Providers the seller connects with their own API key (server: ai_key_catalog.py). */
export type KeyProviderId = "openai" | "anthropic" | "gemini" | "xai" | "mistral" | "openrouter";

export type ListingProviderId = "chatgpt" | "claude" | "mimo" | "cursor" | KeyProviderId;

/** One API-key provider as Settings lists it. The key itself never leaves the server. */
export interface AIKeyProvider {
  id: KeyProviderId;
  label: string;
  key_url: string;
  key_hint: string;
  /** Searches the web itself, so sold comps need no Brave key. */
  web_search: boolean;
  /** Offers its own sign-in that hands Anteroom a key (OpenRouter). */
  sign_in: boolean;
  masked_key: string | null;
  vision_model: string;
  listing_model: string;
}

export interface AIKeyModels {
  models: string[];
  /** The provider's own display name for each model id (OpenRouter supplies these). */
  labels?: Record<string, string>;
  vision_model: string;
  listing_model: string;
  /** OpenRouter's reasoning levels for the listing model, when it takes one. */
  reasoning: { options: { value: string; label: string }[]; value: string } | null;
  error: string | null;
}

/** A starred model in the composer picker, synced through the UI preferences. */
export interface ModelFavorite {
  provider: ListingProviderId;
  model: string;
}

/**
 * One rate-limit window, the shape T3 Code's ServerProviderUsageWindow uses.
 * `id` is stable per provider (`five_hour`, `primary`, `totalPercentUsed`).
 */
export interface ProviderUsageWindow {
  id: string;
  kind: "session" | "weekly" | "monthly" | "other";
  label: string;
  used_percent: number;
  resets_at?: string;
  window_duration_mins?: number;
  description?: string;
}

/**
 * A provider's subscription usage. `unavailable` tells an account that can
 * never report windows (`unsupported`) from a read that failed this time.
 */
export interface ProviderUsageLimits {
  provider: ListingProviderId;
  label: string;
  checked_at: string;
  windows: ProviderUsageWindow[];
  plan?: string;
  unavailable?: { reason: "unsupported" | "probeFailed"; message?: string };
}
/** "same" reads photos with the listing provider. */
export type PhotoProviderChoice = "same" | ListingProviderId;

/** What the sidebar names each listing by. */
export type ListingNames = "title" | "sku";

export interface ProviderStatus {
  provider: string;
  primary: ListingProviderId;
  fallback: ListingProviderId | "none";
  photo_provider: PhotoProviderChoice;
  configured: boolean;
  masked_key: string | null;
  masked_cursor_key?: string | null;
  vision_model: string;
  listing_model: string;
  base_url: string;
  chatgpt: ChatGPTStatus;
  claude: ClaudeStatus;
}

export interface ProviderTestResult {
  ok: boolean;
  provider: string;
  error?: string;
}

/** One Vendoo HTTP call as the log list shows it. Credentials in the URL are masked. */
export interface StudioErrorLogEntry {
  id: number;
  at: string;
  level: "warning" | "error" | "critical";
  source: string;
  message: string;
  detail: string | null;
}

export interface VendooApiLogEntry {
  id: number;
  at: string;
  method: string;
  url: string;
  host: string;
  path: string;
  status: number | null;
  duration_ms: number;
  ok: boolean;
  error: string | null;
}

/** The full call: headers and bodies, with tokens, keys, and cookies masked. */
export interface VendooApiLogDetail extends VendooApiLogEntry {
  request_headers: Record<string, string>;
  request_body: string | null;
  response_headers: Record<string, string>;
  response_body: string | null;
}

export interface FillLogEntry {
  id: string;
  step: string;
  marketplace: string;
  field: string;
  status: string;
  reason: string;
  selector: string;
  value_preview: string;
}

export interface FillLogReport {
  job_id: string;
  summary: Record<string, number>;
  by_marketplace: Record<string, { summary: Record<string, number>; entries: FillLogEntry[] }>;
  log_path: string | null;
}


/** One field a marketplace form renders, as Vendoo's category schema defines it. */
export interface NativeInventoryRow {
  sizeSetId: string;
  sizeId: string;
  quantity: number | string;
  sku?: string;
}

/** One eBay variation: its value for each aspect the listing varies by, stock, and optional SKU and price. */
export interface EbayVariationRow {
  values: Record<string, string>;
  quantity: number | string;
  sku?: string;
  price?: string | number;
}

/** ``ebay_specifics.variations``: one eBay listing with variations. */
export interface EbayVariations {
  aspects: string[];
  photosBy?: string;
  rows: EbayVariationRow[];
  /** Photo IDs for each value of the photos aspect. */
  photos: Record<string, string[]>;
}

/** An aspect eBay lets vary in the category, with its choices. */
export interface EbayVariationAspect {
  name: string;
  selection_only: boolean;
  options: string[];
}

export interface NativeInventorySize {
  id: string;
  label: string;
  /** First equivalent size id in another system, used when the seller switches US/AU/EU/UK. */
  equivalents?: Record<string, string>;
}

export interface NativeInventoryGroup {
  id: string;
  label: string;
  sizes: NativeInventorySize[];
  /** Sizes for each system the category offers. `sizes` stays the US list. */
  systems?: Record<string, NativeInventorySize[]>;
}

export interface MarketplaceFormField {
  key: string;
  label: string;
  value: string | string[] | number | boolean | NativeInventoryRow[] | EbayVariations;
  path?: string;
  /** "note" is text the marketplace's page shows with no control: its label and help. */
  input_type?: "number" | "boolean" | "textarea" | "inventory" | "variations" | "photos" | "note" | "cascade" | "segmented" | "badge" | "datetime-local" | "text";
  inventory_groups?: NativeInventoryGroup[];
  variation_aspects?: EbayVariationAspect[];
  min_value?: number;
  max_value?: number;
  step?: number;
  locked?: boolean;
  option_values?: Record<string, string>;
  clears?: string[];
  help?: string;
  value_constraints?: Record<string, {
    parentName?: string;
    parentValues?: string[];
    applicableForLocalizedAspectName?: string;
    applicableForLocalizedAspectValues?: string[];
  }[]>;
  required: boolean;
  multi: boolean;
  selection_only: boolean;
  options: string[];
  max_values?: number | null;
  max_length?: number | null;
  options_source?: "marketplace" | "vendoo" | "reference" | "saved";
  /** False when this row is saved data the category form does not show. Absent means it is on the form. */
  on_form?: boolean;
  /** Choice label → the heading the marketplace lists it under (Mercari's size groups). */
  option_groups?: Record<string, string>;
  /**
   * A stored value the picker cannot show → the choice it stands for. Mercari
   * lists "S (4-6)" where a tag says just "S", so a saved "S" reads as that
   * size and saving the row unchanged sends Mercari's own id.
   */
  option_aliases?: Record<string, string>;
  /** The button the marketplace's page hides this field behind ("Show more", "See title options"). */
  reveal?: string;
  /** A photos row: the page's caption under each photo slot, in order (Depop: Cover photo, Front, …). */
  slot_labels?: string[];
  /** File-dialog filter for a photos row. Depop's page takes JPEG and PNG only. */
  accept?: string;
  /** The heading this field sits under on the marketplace's own form. */
  section?: string;
  /** Half, a third or a quarter of a row, as the marketplace's form lays it out. Absent means a full row. */
  width?: "half" | "third" | "quarter";
  /** The control is half the content column and still starts its own row. */
  control_width?: "half";
  /** Department, then category, then subcategory. The closed category reads "Women Jeans". */
  cascade?: { id: string; label: string; items: unknown[] }[];
  /** Shipping-discount id to the dollars the seller pays, for Your Earnings. */
  option_amounts?: Record<string, number>;
  /** A question the page asks over several consecutive inputs ("How heavy will the package be?"). */
  group?: string;
  group_help?: string;
  /** The line the page shows under a section heading, carried on the section's first field. */
  section_help?: string;
  placeholder?: string;
  /** Text inside the input before or after the value, such as "$" or "lb". */
  prefix?: string;
  suffix?: string;
  /** The page shows no label above this input; the label stays for screen readers. */
  label_hidden?: boolean;
  /** Show the typed length beside the label, as "16/80". */
  counter?: boolean;
  /** Choices shown as radio buttons (stacked, or in one row), or a switch shown as a button. */
  presentation?: "radio" | "radio-row" | "button";
  /** Choice label → the line the page shows under that choice. */
  option_help?: Record<string, string>;
  /** True when choosing a different category adds or removes this row. */
  category_specific?: boolean;
  /** Depop's "Hashtags: N" remaining count, beside the character counter. */
  hashtag_max?: number;
  /** The seller can keep this value as the default every new listing starts with. */
  save_as_default?: boolean;
}

/** A marketplace's form for one listing. ``known`` is false when no schema is cached. */
export interface MarketplaceForm {
  account_connected?: boolean;
  marketplace: string;
  native?: boolean;
  /** Labels are exactly the page's, required markers included, so none is added. */
  page_labels?: boolean;
  notes?: string[];
  category_id: string;
  known: boolean;
  fields: MarketplaceFormField[];
}


/** Progress of a whole-inventory Vendoo import. */
export interface VendooBulkImport {
  running: boolean;
  total: number;
  processed: number;
  imported: number;
  updated: number;
  skipped: number;
  failed: number;
  deleted: number;
  photos: number;
  current_title: string;
  started_at: string;
  finished_at: string;
  cancelled: boolean;
  error: string;
  failures: { item_id: string; title: string; error: string }[];
}

/** Quiet pass that only refreshes draft / active / sold for bound listings. */
export interface VendooLabelSync {
  running: boolean;
  checked: number;
  updated: number;
  skipped: boolean;
  reason: string;
  started_at: string;
  finished_at: string;
  error: string;
}

/** Stored labels that are Vendoo IDs: how many were (or can be) renamed, and the IDs with no known name. */
export interface VendooLabelConversion {
  items: number;
  converted: number;
  unknown: string[];
}

export interface SimilarItem {
  id: string;
  title: string;
  status: string;
  brand: string | null;
  size: string | null;
  price: number | null;
  sku: string | null;
  cover_url: string | null;
}

export type SimilarReason = "same_photo" | "same_sku" | "similar_title" | "similar_photo";

export interface SimilarPair {
  first: SimilarItem;
  second: SimilarItem;
  reasons: SimilarReason[];
  score: number;
  photo_distance: number | null;
  title_score: number | null;
}

export interface SimilarItems {
  total: number;
  pairs: SimilarPair[];
}

export interface SimilarMerge {
  kept_id: string;
  merged_id: string;
  marketplaces: string[];
  sales: number;
  labels: string[];
}

export interface Suggestion {
  conversation_id: string;
  kind: SuggestionKind;
  title: string;
  reason: string;
  action: "open" | string;
  score: number;
  cover_photo_url?: string | null;
  age_days?: number | null;
  /** Stale only: items sharing this one's brand or category that sold in the last 60 days. */
  similar_sold?: number;
  /** Stale only: whether buyers are shopping this item's season now; null for all-year or unknown. */
  season_fit?: "in" | "off" | null;
}

export type SuggestionKind =
  | "failed"
  | "ready_to_generate"
  | "fix_validation"
  | "stale_active"
  | "ready_to_review";

export interface SuggestionsResponse {
  suggestions: Suggestion[];
}

/** An item live on Etsy that Etsy does not allow. Studio never delists it. */
export interface EtsyNotAllowedItem {
  conversation_id: string;
  title: string;
  when_made: string;
  who_made: string;
  listed_at: string | null;
  etsy_url: string | null;
  /** The title dates the piece, so When Made may be what is wrong. */
  title_says_vintage: boolean;
}

export interface EtsyNotAllowedResponse {
  items: EtsyNotAllowedItem[];
}

export type AnalyticsRange = "wtd" | "mtd" | "ytd" | "7d" | "30d" | "90d" | "12m" | "all" | "custom";

export interface AnalyticsInventory {
  active: number;
  draft: number;
  sold: number;
  failed: number;
  working: number;
  asking_value: number;
  cost_value: number;
  cost_known: number;
  stale_count: number;
  stale_value: number;
  undated_count: number;
}

export interface AnalyticsSales {
  count: number;
  revenue: number;
  profit: number | null;
  profit_known: number;
  fees_known: number;
  revenue_known: number;
  days_known: number;
  average_price: number | null;
  median_days: number | null;
  margin: number | null;
}

export interface AnalyticsPeriod extends AnalyticsSales {
  label: string;
}

export interface AnalyticsGroup extends AnalyticsSales {
  id: string;
  label: string;
}

export interface AnalyticsLabelGroup extends AnalyticsGroup {
  color: LabelColor | null;
}

export interface AnalyticsActiveListing {
  conversation_id: string;
  title: string;
  price: number;
  days_listed: number | null;
}

export interface AnalyticsAging {
  label: string;
  count: number;
  asking_value: number;
  listings: AnalyticsActiveListing[];
}

/** An active listing past the seller's stale threshold, with how deep it can go and still cover its cost. */
export interface AnalyticsStaleListing {
  conversation_id: string;
  title: string;
  days_listed: number;
  price: number;
  cost: number | null;
  /** Whole dollars that return the cost after fees; null with no cost recorded. */
  lowest_price: number | null;
  /** Null when even the everyday 25% would sell below the lowest price. */
  discount_percent: number | null;
  sale_price: number | null;
}

export interface AnalyticsSale {
  conversation_id: string;
  title: string;
  price: number | null;
  marketplace: string;
  sold_at: string | null;
  days_listed: number | null;
  /** The sale event this sale fell in, if any. */
  event: string | null;
  profit: number | null;
}

export interface InventoryAnalytics {
  range: AnalyticsRange;
  last_updated_at: string | null;
  incomplete_sales: { conversation_id: string; title: string; missing: string[] }[];
  undated_sales: number;
  periods_truncated: boolean;
  inventory: AnalyticsInventory;
  sell_through_rate: number | null;
  sales: AnalyticsSales;
  previous: { start: string; end: string; sales: AnalyticsSales } | null;
  periods: AnalyticsPeriod[];
  marketplaces: AnalyticsGroup[];
  categories: AnalyticsGroup[];
  brands: AnalyticsGroup[];
  labels: AnalyticsLabelGroup[];
  /** Every sale-price band, low to high: budget, standard, premium, high. */
  price_tiers: AnalyticsGroup[];
  aging: AnalyticsAging[];
  stale: AnalyticsStaleListing[];
  /** Days before a listing counts as stale (Settings → Listings → Inventory). */
  stale_days?: number;
  recent: AnalyticsSale[];
  oldest: { conversation_id: string; title: string; price: number; days_listed: number }[];
  ads: AnalyticsAds;
}

export interface BackupSnapshot {
  path: string;
  name: string;
  taken_at: string;
  reason: string;
  size_bytes: number;
  compressed: boolean;
}

export interface BackupsStatus {
  snapshots: BackupSnapshot[];
  folder: string | null;
  latest: BackupSnapshot | null;
  database_bytes?: number;
  retained_bytes?: number;
  disk_free_bytes?: number;
  can_snapshot?: boolean;
  warning?: string | null;
}

export interface DatabaseTableSize {
  table: string;
  bytes: number | null;
  rows: number;
}

export interface DatabaseReport {
  path: string;
  database_bytes: number;
  tables: DatabaseTableSize[];
  ok?: boolean;
  pruned?: { vendoo_drafts: number; duplicate_events: number; deleted: number };
}

export interface SourcingComp {
  url: string;
  title: string;
  sold_at: string;
  price: number;
  marketplace: string;
  snippet: string;
  /** Set when the sale is one of the seller's own listings. */
  conversation_id?: string | null;
}

/** Everything behind one kind of box's resale estimate. */
export interface SourcingEvidence {
  theme: string;
  per_piece: number;
  sold_median: number;
  active_median: number | null;
  low: number;
  high: number;
  comps: SourcingComp[];
  active: Omit<SourcingComp, "sold_at">[];
  updated_at: string | null;
  source: string | null;
}

export interface SourcingLot {
  store: string;
  /** Shopify variant number, or a marketplace lot id. */
  variant_id: number | string;
  title: string;
  url: string;
  price: number;
  compare_at: number | null;
  pcs: number;
  pcs_estimated: boolean;
  grade: string;
  lbs: number;
  lbs_estimated: boolean;
  vip: boolean;
  listed: string | null;
  seller_resale: number | null;
  theme: string;
  /** Marketplace lots: the seller, the state it ships from, and whether shipping is in the price. */
  seller: string | null;
  origin: string | null;
  free_shipping: boolean;
  checkout_fee: number;
  ship_est: number;
  /** The carrier's list rate before the store's discount; what a bought box records. */
  ship_list: number;
  usable_pcs: number;
  demand: number;
  trend_hits: string[];
  landed: number;
  cog_per_pc: number;
  cog_per_usable_pc: number;
  resale_per_pc: number | null;
  resale_factor: number;
  sell_through: number;
  expected_revenue: number | null;
  expected_profit: number | null;
  roi: number | null;
  score: number;
  comps_count: number;
  research_at: string | null;
  research_source: string | null;
  active_median: number | null;
  resale_low: number | null;
  operating_cost: number;
  break_even_pcs: number | null;
  downside_profit: number | null;
}

export interface SourcingCart {
  store: string;
  name: string;
  subtotal: number;
  shipping: number;
  free_shipping: boolean;
  free_shipping_over: number | null;
  /** Whether the link opens a cart with the boxes in it, or the first box's own page. */
  cart_fills: boolean;
  cart_url: string;
  lots: SourcingLot[];
}

/** One of a supplier's best boxes judged on its own: whether it passes every check, or the first it fails. */
export interface SourcingPick extends SourcingLot {
  check: "qualifies" | "return_target" | "downside" | "needs_research" | "budget";
}

export interface SourcingBuyList {
  budget: number;
  total: number;
  expected_profit: number;
  carts: SourcingCart[];
  exclusions: Record<string, "needs_research" | "same_theme" | "theme_stocked" | "return_target" | "downside" | "budget" | "alternative">;
}

export interface SourcingSeasonality {
  window: { buy_on: string; start_date: string; end_date: string; ready_in_weeks: number; selling_window_weeks: number; market: string; timezone: string };
  seller_history: {
    periods: { start_date: string; end_date: string }[];
    dated_recorded_sales: number;
    matching_window_sales: number;
    excluded_incomplete_or_invalid: number;
    minimum_group_sales: number;
    coverage: string;
    groups: {
      category_path: string;
      item_type: string | null;
      count: number;
      historical_median_price: number;
      period_counts: Record<string, number>;
      examples: { title: string; sold_at: string; sold_price: number; listing_source: string }[];
    }[];
  };
}

export interface SourcingSnapshot {
  updated_at: string;
  destination_zip: string;
  preferences: Omit<SourcingPrefs, "recent_zips" | "fees_auto" | "sell_through_auto" | "cost_per_piece_auto" | "selling_window_auto">;
  stores: Record<string, { name: string; error: string | null; sellout: number | null; zone: number | null; origins: number }>;
  research: boolean;
  priced_themes: number;
  shipping: {
    residential_surcharge: number;
    fuel_surcharge_pct: number;
    fuel_surcharge_as_of: string;
    factors: Record<string, number>;
    calibration: Record<string, { factor: number | null; orders: number; needed: number }>;
  };
  assumptions: { market_haircut?: { factor: number; themes: number } | null; sell_through: number; sell_through_measured: { rate: number; sales: number } | null; fees: number; fees_measured: { rate: number; sales: number } | null; grade_yield: Record<string, number>; cost_per_piece: number; cost_per_piece_measured: { value: number; sales: number } | null; selling_window_measured: { value: number; sales: number } | null };
  buy_list: SourcingBuyList;
  store_buy_lists: Record<string, SourcingBuyList>;
  /** Each supplier's five best boxes on every check, however few qualify. */
  store_picks: Record<string, SourcingPick[]>;
  lots: SourcingLot[];
  calibration: Record<string, { factor: number | null; sales: number; needed: number }>;
  seasonality: SourcingSeasonality;
  /** What Raghouse's members-only boxes would add to this list; null when the seller is a VIP. */
  vip_upside: { boxes: number; extra_profit: number; monthly_fee: number } | null;
}

export interface SourcingPrefs {
  budget: number;
  min_roi: number;
  raghouse_vip: boolean;
  zip: string;
  recent_zips: string[];
  sell_through: number;
  sell_through_auto: boolean;
  fees: number;
  fees_auto: boolean;
  cost_per_piece: number;
  cost_per_piece_auto: boolean;
  ready_in_weeks: number;
  selling_window_weeks: number;
  selling_window_auto: boolean;
}

export interface SourcingState {
  refreshing: boolean;
  ebay_connected: boolean;
  prefs: SourcingPrefs;
  trend: { terms: string[]; updated_at: string | null; source: string | null };
  snapshot: SourcingSnapshot | null;
}

/** What a bought box, or a store's boxes together, have returned so far. */
export interface BoxTotals {
  spent: number;
  listings: number;
  listed: number;
  sold: number;
  returned: number;
  profit: number;
  roi: number | null;
  /** Of the sellable pieces when the counts are known, else of the listings made. */
  sell_through: number | null;
  /** Sold within 30, 60 and 90 days of buying; only windows the boxes are old enough for. */
  sell_through_by_age: { days: number; sold: number; of: number; rate: number | null }[];
  unsold_asking: number;
  median_days: number | null;
  /** Average sale price. */
  asp: number | null;
  /** Sales at the premium price or above. */
  sold_premium: number;
  /** Null until the seller records how many pieces were not worth listing. */
  unsellable: number | null;
  unsellable_rate: number | null;
}

export interface SourceBox extends BoxTotals {
  id: string;
  /** "raghouse", "tvf", or the name of any other store. */
  store: string;
  title: string;
  url: string | null;
  price: number;
  shipping: number;
  pieces: number | null;
  /** Pieces less the unsellable ones, once the piece count is known. */
  sellable: number | null;
  estimate_per_piece: number | null;
  list_shipping: number | null;
  bought_at: string | null;
  /** The box's delivered cost spread over its sellable pieces. */
  cost_per_piece: number | null;
}

export interface SourceStoreResults extends BoxTotals {
  store: string;
  boxes: number;
}

export interface SourceBoxes {
  boxes: SourceBox[];
  stores: SourceStoreResults[];
}

export interface SourceBoxInput {
  store: string;
  title: string;
  price: number;
  shipping: number;
  pieces: number | null;
  unsellable?: number | null;
  url?: string | null;
  estimate_per_piece?: number | null;
  list_shipping?: number | null;
}

export type ScoutVerdict = "buy" | "maybe" | "pass" | "unsure";

/** One "is this worth buying?" check from photos taken while sourcing. */
/** One measurement read from yardstick photos: a value only when two reads agree within 1/4 in. */
export interface MeasurementRead {
  status: "accepted" | "disagree" | "cannot_read";
  value?: number;
  reads: (number | string | null)[];
}

export interface MeasurementReadResult {
  photos_read: number;
  /** Keyed like the bulk upload's measurement fields (pitToPit, length, waist, legOpening, ...). */
  measurements: Record<string, MeasurementRead>;
}

export interface ScoutCheck {
  id: string;
  status: "checking" | "done" | "failed";
  asking_price: number | null;
  photo_urls: string[];
  title: string | null;
  /** Expected sale price: the median of the sold comps. */
  estimate: number | null;
  comps_count: number | null;
  comps: string | null;
  error: string | null;
  decision: "bought" | "passed" | null;
  conversation_id: string | null;
  sold_price: number | null;
  created_at: string | null;
  /** What the sale brings in after marketplace fees. */
  net: number | null;
  /** The most worth paying to double the money. */
  pay_up_to: number | null;
  profit: number | null;
  verdict: ScoutVerdict | null;
}

export interface ScoutState {
  checks: ScoutCheck[];
  track_record: { checks: number; bought: number; sold: number; median_ratio: number | null; close: number };
}

