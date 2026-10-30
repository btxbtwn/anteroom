import { hostedBrowser } from "./hostedBrowser";
import { studioFetch, hasHostedApiSession } from "./transport";
import type { ListingTemplate, TemplateSource, TemplateSave, TemplateDraft, TemplateApply, TemplateReview, TemplateApproval } from "./listingTemplates";
import type { CopyPreview, CopyListingRequest, BatchCopyPreviewRow } from "./listingCopy";
import type { ListingVideo, ListingVideoLibrary, MarketplaceMediaRules } from "./listingVideos";
import type { BulkEdits, BulkReviewRow, BulkReviewToken, BulkEditResult } from "./bulkEdit";
import type { BulkDeleteResult, BulkDeleteRow } from "./bulkDelete";
import type { LabelColor } from "../labelColors";
import type { BulkMarketplaceAction, BulkMarketplacePerform, BulkMarketplacePreview, BulkMarketplaceResult } from "./bulkMarketplace";
import { salesLedgerParams, type FinancialSave, type SalesLedgerFilters, type SalesLedgerPage, type SalesLedgerRow } from "./salesLedger";
import type { OrderRecoveryRequest, OrderRecoveryResult } from "./orderRecovery";
import type {
  Promotion, PromotionActivity, PromotionAutomation, PromotionAutomationBody, PromotionAutomations, PromotionCoverage, PromotionFloor,
  PromotionHistory, PromotionMarketplace, PromotionPreview, PromotionPreviewRequest, PromotionResult,
} from "./promotions";
import type {
  SendPreview,
  BackupSnapshot,
  BackupsStatus,
  Conversation,
  ConversationSummary,
  DatabaseReport,
  DeleteConversationResult,
  FillLogReport,
  Job,
  ListingData,
  ListingNames,
  ChatAttachmentUpload,
  ListingProviderId,
  AIKeyModels,
  AIKeyProvider,
  KeyProviderId,
  ModelFavorite,
  ProviderUsageLimits,
  PhotoProviderChoice,
  ListingResponse,
  ListingRevision,
  ListingUpdateResult,
  ConversationActivity,
  MarkdownApplyResult,
  MarkdownDropRequest,
  MarkdownPreviewRow,
  Message,
  OkResponse,
  Photo,
  PhotoEdits,
  PhotoUploadResult,
  PriceDropApplyResult,
  PriceDropCompsSource,
  PriceDropPreview,
  ProviderStatus,
  ProviderTestResult,
  StudioErrorLogEntry,
  VendooApiLogDetail,
  VendooApiLogEntry,
  ChatGPTPendingLogin,
  RevisionRestoreResult,
  ValidationResult,
  VendooBulkImport,
  VendooLabelSync,
  VendooLabelConversion,
  SimilarItems,
  SimilarMerge,
  VendooItemResult,
  MarketplaceForm,
  SuggestionsResponse,
  EtsyNotAllowedResponse,
  AnalyticsRange,
  InventoryAnalytics,
  MeasurementReadResult,
  ScoutCheck,
  ScoutState,
  SourceBoxInput,
  SourceBoxes,
  SourcingPrefs,
  SourcingEvidence,
  SourcingState,
} from "./types";
import { readSse } from "./sse";
import { AssistantRefused, type AssistantMemory, type AssistantMessage, type AssistantStreamHandlers, type AssistantThread } from "./assistant";
import type { Automation, AutomationApproval, AutomationBody, AutomationDraft, AutomationHistory, AutomationPreview, AutomationRun, RelistGap } from "./automations";
import type { SaleCalendarData, SaleEventStatus, SalePlan, SaleRecord } from "./saleCalendar";
import type { AgentConnectionStatus, AgentConnectionTest } from "./agentConnection";
import type { DirectConnectionsStatus, DirectMarketplace, MarketplaceSale, MarketplaceSaleState, SaleDetectionStatus } from "./directConnections";
import type { DelistMarketplace, FacebookCheck, MarketplaceListings, MarketplaceMark, SalesCheck, SellerSale, SellerSaleResult } from "./marketplaceListings";
import type { NativeDraft, NativeDrafts, NativeMarketplace, NativePreflight, SellwildChoice, SellwildOptions } from "./nativeMarketplaces";
import type { ExtensionSessionConnect, ExtensionSessionMarket, ExtensionSessions } from "./marketplaceSessions";
import type { EbayOffer, EbayOfferSelection, EbayOffersStatus } from "./ebayOffers";
import type { EbayListing, EbayPageData, EbayPayload, EbayResult, EbayReview } from "./ebay";
import type { ChromeImportMarketplace, MarketplaceImportPage, MarketplaceImportResult } from "./inventoryImport";
import type { EtsyListing, EtsyListingState, EtsyOrders, EtsyOrderStatus, EtsyPage, EtsyShop } from "./etsy";

const BASE = "/api";

type ErrorBody = { detail?: unknown; message?: unknown } | null | undefined;
type ErrorItem = { msg?: string; message?: string } | string | null | undefined;

export function errorMessage(body: ErrorBody, fallback: string): string {
  const detail = body?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item: ErrorItem) => (typeof item === "string" ? item : item?.msg || item?.message || ""))
      .filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  if (detail && typeof detail === "object") {
    const record = detail as { message?: unknown; errors?: unknown };
    if (typeof record.message === "string" && record.message.trim()) return record.message;
    if (Array.isArray(record.errors)) {
      const messages = record.errors
        .map((item: ErrorItem) => (typeof item === "string" ? item : item?.message || item?.msg))
        .filter(Boolean);
      if (messages.length) return messages.join("; ");
    }
  }
  if (typeof body?.message === "string" && body.message.trim()) return body.message;
  return fallback;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await studioFetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errorMessage(body, `Request failed: ${res.status}`));
  }
  return res.json();
}

async function requestWithTimeout<T>(
  path: string,
  options: RequestInit,
  timeoutMessage: string,
  timeoutMs = 30000,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await request<T>(path, { ...options, signal: controller.signal });
  } catch (error) {
    if (controller.signal.aborted) throw new Error(timeoutMessage);
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export interface VendooSyncStatus {
  checked_at: string | null;
  conflict: boolean;
  revision_id: string | null;
}

export interface BrowserRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface BrowserViewport {
  width: number;
  height: number;
  scroll_x?: number;
  scroll_y?: number;
}

export interface BrowserField {
  marketplace: string;
  label: string;
  key: string;
  selector: string;
  value: string;
  filled: boolean;
  required: boolean;
  disabled: boolean;
  is_dropdown: boolean;
  account_managed: boolean;
  error: string;
  options: string[];
  rect: BrowserRect;
}

export interface BrowserInputEvent {
  kind: "mouse" | "touch" | "key" | "text";
  type?: string;
  x_ratio?: number;
  y_ratio?: number;
  button?: "none" | "left" | "middle" | "right";
  buttons?: number;
  click_count?: number;
  delta_x?: number;
  delta_y?: number;
  modifiers?: number;
  key?: string;
  code?: string;
  text?: string;
}

async function followAssistant(
  path: string,
  body: string | undefined,
  handlers: AssistantStreamHandlers,
  signal?: AbortSignal,
): Promise<{ error: string }> {
  const res = await studioFetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body,
    cache: "no-store",
    signal,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new AssistantRefused(errorMessage(detail, `Request failed: ${res.status}`));
  }
  let error = "";
  await readSse(res, (event, data) => {
    if (event === "error") error = data;
    else handlers.onEvent(event, data);
  });
  return { error };
}

export const api = {
  marketplaceMedia: {
    rules: () => request<MarketplaceMediaRules>("/marketplace-media"),
  },
  listingVideos: {
    list: (convId: string) => request<ListingVideoLibrary>(`/conversations/${convId}/videos`),
    delete: (convId: string, videoId: string) => request<OkResponse>(`/conversations/${convId}/videos/${videoId}`, { method: "DELETE" }),
    upload: async (convId: string, file: File): Promise<ListingVideo> => {
      const form = new FormData();
      form.append("file", file);
      const response = await studioFetch(`${BASE}/conversations/${convId}/videos`, { method: "POST", body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The video could not be uploaded.");
      return data;
    },
  },
  backups: {
    list: () => request<BackupsStatus>("/backups"),
    create: () => request<{ ok: boolean; snapshot: BackupSnapshot }>("/backups", { method: "POST" }),
    setFolder: (folder: string | null) =>
      request<{ ok: boolean; folder: string | null }>("/backups/folder", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder }),
      }),
  },
  catalog: {
    status: () => request<{running: boolean; complete: boolean; marketplaces: Record<string,
      {status: string; nodes: number; pending_branches: number; error: string | null}>}>("/catalog/sync"),
    sync: () => request<{started: boolean}>("/catalog/sync", {method: "POST"}),
    dropdownOptions: () =>
      request<{ forms: Record<string, Record<string, string[]>> }>("/catalog/dropdown-options"),
    searchCategories: (q: string, marketplace: string) =>
      request<{ results: { kind: string; marketplace: string; id: string; path: string; label: string }[] }>(
        `/catalog/search?${new URLSearchParams({ q, marketplace, kind: "category", top_k: "50" })}`),
    categories: (marketplace: string) =>
      request<{ coverage: string; complete: boolean; nodes: {
        id?: string; parent_id?: string; path: string; label: string; is_leaf?: boolean; has_children?: boolean;
      }[] }>(`/catalog/categories?marketplace=${encodeURIComponent(marketplace)}`),
  },
  suggestions: {
    list: () => request<SuggestionsResponse>("/suggestions"),
  },
  chromeImport: {
    importCandidates: (marketplace: ChromeImportMarketplace, cursor: string | null) =>
      request<MarketplaceImportPage>(`/chrome-import/${marketplace}/active${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`),
    importListing: (marketplace: ChromeImportMarketplace, listing: string, linkConversationId?: string) =>
      request<MarketplaceImportResult>(`/chrome-import/${marketplace}`, {
        method: "POST", body: JSON.stringify({ listing, ...(linkConversationId ? { link_conversation_id: linkConversationId } : {}) }),
      }),
  },
  etsy: {
    importCandidates: (page: number) => request<MarketplaceImportPage>(`/etsy/import/active?page=${page}`),
    importListing: (listingId: string) => request<MarketplaceImportResult>("/etsy/import", {
      method: "POST", body: JSON.stringify({ listing_id: listingId }),
    }),
    notAllowed: () => request<EtsyNotAllowedResponse>("/etsy/not-allowed"),
    shop: () => request<EtsyShop>("/etsy/shop"),
    orders: (params: { start: string; end: string; offset: number; status: EtsyOrderStatus }) =>
      request<EtsyOrders>(`/etsy/orders?${new URLSearchParams({ ...params, offset: String(params.offset) })}`),
    listings: (state: EtsyListingState, offset: number) =>
      request<EtsyPage<EtsyListing>>(`/etsy/listings?${new URLSearchParams({ state, offset: String(offset) })}`),
    listingProfiles: () => request<{
      shipping: { id: string; title: string; is_calculated: boolean }[];
      processing: { id: string; title: string }[];
      returns: { id: string; title: string }[];
    }>("/etsy/listing-profiles"),
    listingDefaults: () => request<{ shipping_profile_id?: string; readiness_state_id?: string; return_policy_id?: string }>("/etsy/listing-defaults"),
    setListingDefaults: (shippingProfileId: string, readinessStateId: string, returnPolicyId: string) =>
      request<{ shipping_profile_id?: string; readiness_state_id?: string; return_policy_id?: string }>("/etsy/listing-defaults", {
        method: "PUT", body: JSON.stringify({
          shipping_profile_id: shippingProfileId, readiness_state_id: readinessStateId, return_policy_id: returnPolicyId,
        }),
      }),
  },
  analytics: {
    get: (range: AnalyticsRange, custom?: { start: string; end: string }) => {
      const params = new URLSearchParams({ range });
      if (custom) {
        params.set("start", custom.start);
        params.set("end", custom.end);
      }
      return request<InventoryAnalytics>(`/analytics?${params}`);
    },
  },
  measurements: {
    /** Suggest measurements from an item's yardstick photos. Nothing is saved. */
    read: async (files: File[], garment: string): Promise<MeasurementReadResult> => {
      const form = new FormData();
      files.forEach((file) => form.append("files", file));
      form.append("garment", garment);
      const response = await studioFetch(`${BASE}/measurements/read`, { method: "POST", body: form });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorMessage(data, `Reading failed: ${response.status}`));
      return data as MeasurementReadResult;
    },
    /** The same, from a saved listing's photos. */
    readSaved: (convId: string, garment: string) =>
      request<MeasurementReadResult>(`/conversations/${convId}/measurements/read`, {
        method: "POST",
        body: JSON.stringify({ garment }),
      }),
  },
  scout: {
    list: () => request<ScoutState>("/scout"),
    create: async (files: File[], askingPrice: number | null): Promise<ScoutCheck> => {
      const form = new FormData();
      files.forEach((file) => form.append("files", file));
      if (askingPrice != null) form.append("asking_price", String(askingPrice));
      const response = await studioFetch(`${BASE}/scout`, { method: "POST", body: form });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorMessage(data, `Check failed: ${response.status}`));
      return data as ScoutCheck;
    },
    setAskingPrice: (id: string, askingPrice: number | null) =>
      request<ScoutCheck>(`/scout/${id}`, { method: "PATCH", body: JSON.stringify({ asking_price: askingPrice }) }),
    decide: (id: string, decision: "bought" | "passed") =>
      request<ScoutCheck>(`/scout/${id}/decision`, { method: "POST", body: JSON.stringify({ decision }) }),
  },
  assistant: {
    threads: () => request<AssistantThread[]>("/assistant/threads"),
    createThread: () => request<AssistantThread>("/assistant/threads", { method: "POST" }),
    deleteThread: (id: string) => request<{ ok: boolean }>(`/assistant/threads/${id}`, { method: "DELETE" }),
    messages: (threadId?: string) =>
      request<AssistantMessage[]>(`/assistant/messages${threadId ? `?thread_id=${encodeURIComponent(threadId)}` : ""}`),
    /** What the seller told the assistant about how they run the shop. */
    note: () => request<{ note: string }>("/assistant/note"),
    saveNote: (note: string) => request<{ note: string }>("/assistant/note", { method: "PUT", body: JSON.stringify({ note }) }),
    /** Facts the assistant learned in chat and keeps after a conversation is cleared. */
    memory: () => request<{ memories: AssistantMemory[] }>("/assistant/memory"),
    forget: (id: string) =>
      request<{ memories: AssistantMemory[] }>(`/assistant/memory/${encodeURIComponent(id)}`, { method: "DELETE" }),
    /**
     * Ask, and follow the answer. Studio keeps writing it if this connection
     * drops; `resume` picks it back up. Resolves with the assistant's own
     * error, if it reported one.
     */
    ask: (
      text: string,
      handlers: AssistantStreamHandlers,
      signal?: AbortSignal,
      attachments: ChatAttachmentUpload[] = [],
      threadId?: string,
    ) =>
      followAssistant(
        "/assistant/messages",
        JSON.stringify({ text, ...(attachments.length ? { attachments } : {}), ...(threadId ? { thread_id: threadId } : {}) }),
        handlers,
        signal,
      ),
    /** Follow the answer in progress from its start; ends at once when there is none, or when it belongs to another thread. */
    resume: (handlers: AssistantStreamHandlers, signal?: AbortSignal, threadId?: string) =>
      followAssistant(
        `/assistant/messages/resume${threadId ? `?thread_id=${encodeURIComponent(threadId)}` : ""}`,
        undefined,
        handlers,
        signal,
      ),
    /** Stop the answer in progress; what it had written is kept. */
    stop: () => request<{ ok: boolean }>("/assistant/messages/stop", { method: "POST" }),
    /** Write the changes an answer proposed. Resolves with the answer and what became of each listing. */
    applyChanges: (messageId: string) =>
      request<AssistantMessage>(`/assistant/messages/${messageId}/changes/apply`, { method: "POST" }),
    dismissChanges: (messageId: string) =>
      request<AssistantMessage>(`/assistant/messages/${messageId}/changes/dismiss`, { method: "POST" }),
    /** Save the automation an answer drafted. It does not run until its schedule is due, or you run it. */
    confirmAutomation: (messageId: string) =>
      request<AssistantMessage>(`/assistant/messages/${messageId}/automation/confirm`, { method: "POST" }),
    dismissAutomation: (messageId: string) =>
      request<AssistantMessage>(`/assistant/messages/${messageId}/automation/dismiss`, { method: "POST" }),
  },
  automations: {
    list: () => request<Automation[]>("/automations"),
    draft: (prompt: string, timezone?: string) =>
      request<AutomationDraft>("/automations/draft", { method: "POST", body: JSON.stringify({ prompt, timezone }) }),
    preview: (body: AutomationBody) =>
      request<AutomationPreview>("/automations/preview", { method: "POST", body: JSON.stringify(body) }),
    create: (body: AutomationBody) =>
      request<Automation>("/automations", { method: "POST", body: JSON.stringify(body) }),
    update: (id: string, body: AutomationBody) =>
      request<Automation>(`/automations/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    pause: (id: string) => request<Automation>(`/automations/${id}/pause`, { method: "POST" }),
    resume: (id: string) => request<Automation>(`/automations/${id}/resume`, { method: "POST" }),
    remove: (id: string) => request<{ ok: boolean; id: string }>(`/automations/${id}`, { method: "DELETE" }),
    run: (id: string, dryRun = false) =>
      request<AutomationRun>(`/automations/${id}/run`, { method: "POST", body: JSON.stringify({ dry_run: dryRun }) }),
    history: (id: string) => request<AutomationHistory>(`/automations/${id}/history`),
    approvals: () => request<AutomationApproval[]>("/automations/approvals"),
    approve: (row: AutomationApproval) =>
      request<AutomationApproval>(`/automations/approvals/${row.id}/approve`, { method: "POST", body: JSON.stringify({ confirm: true, action: row.action, marketplaces: row.marketplaces, revision_id: row.revision_id, review_token: row.review_token }) }),
  },
  errors: {
    list: () => request<RelistGap[]>("/errors"),
  },
  saleCalendar: {
    record: (record: SaleRecord) => request<{ id: string }>("/analytics/calendar/records", { method: "POST", body: JSON.stringify(record) }),
    editRecord: (id: string, record: SaleRecord) => request<{ id: string }>(`/analytics/calendar/records/${id}`, { method: "PUT", body: JSON.stringify(record) }),
    get: (timezone: string) => request<SaleCalendarData>(`/analytics/calendar?timezone=${encodeURIComponent(timezone)}`),
    create: (plan: SalePlan) => request<{ id: string }>("/analytics/calendar", { method: "POST", body: JSON.stringify(plan) }),
    update: (id: string, plan: SalePlan) => request<{ id: string }>(`/analytics/calendar/${id}`, { method: "PUT", body: JSON.stringify(plan) }),
    status: (id: string, status: SaleEventStatus) => request<{ ok: boolean }>(`/analytics/calendar/${id}/status`, { method: "PATCH", body: JSON.stringify({ status }) }),
    remove: (id: string) => request<{ ok: boolean }>(`/analytics/calendar/${id}`, { method: "DELETE" }),
  },

  boxes: {
    list: () => request<SourceBoxes>("/boxes"),
    create: (body: SourceBoxInput) =>
      request<SourceBoxes>("/boxes", { method: "POST", body: JSON.stringify(body) }),
    update: (id: string, body: Partial<SourceBoxInput>) =>
      request<SourceBoxes>(`/boxes/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    remove: (id: string) => request<SourceBoxes>(`/boxes/${id}`, { method: "DELETE" }),
  },
  sourcing: {
    get: () => request<SourcingState>("/sourcing"),
    refresh: () => request<SourcingState>("/sourcing/refresh", { method: "POST" }),
    evidence: (theme: string) =>
      request<SourcingEvidence>(`/sourcing/evidence?theme=${encodeURIComponent(theme)}`),
    savePrefs: (prefs: Partial<SourcingPrefs>) =>
      request<SourcingState>("/sourcing/prefs", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(prefs),
      }),
  },
  ebay: {
    importCandidates: (page: number) => request<MarketplaceImportPage>(`/ebay/import/active?page=${page}`),
    importListing: (listingId: string) => request<MarketplaceImportResult>("/ebay/import", {
      method: "POST", body: JSON.stringify({ listing_id: listingId }),
    }),
    read: (section: string, params: Record<string, string | number> = {}) =>
      request<EbayPageData>(`/ebay/read/${section}?${new URLSearchParams(Object.entries(params).map(([key, value]) => [key, String(value)]))}`),
    listing: (id: string) => request<EbayListing>(`/ebay/listing/${encodeURIComponent(id)}`),
    preview: (kind: string, payload: EbayPayload) => request<EbayReview>("/ebay/actions/preview", {
      method: "POST", body: JSON.stringify({ kind, payload }),
    }),
    confirm: (id: string, kind: string, payload: EbayPayload) => request<EbayResult>("/ebay/actions/confirm", {
      method: "POST", body: JSON.stringify({ id, kind, payload, confirm: true }),
    }),
    checked: (id: string) => request<OkResponse>(`/ebay/actions/${encodeURIComponent(id)}/checked`, {
      method: "POST", body: JSON.stringify({ confirm: true }),
    }),
    grant: (features: string[]) => request<{ authorizeUrl: string }>("/marketplace-accounts/ebay/connect", {
      method: "POST", body: JSON.stringify({ features }),
    }),
    imageSearch: (data: string, mime: string, offset = 0) => request<EbayPageData>(`/ebay/research/image?offset=${offset}`, {
      method: "POST", body: JSON.stringify({ data, mime }),
    }),
    listingProfiles: () => request<{
      fulfillment: { id: string; title: string }[];
      payment: { id: string; title: string }[];
      return: { id: string; title: string }[];
      locations: { id: string; title: string }[];
    }>("/ebay/listing-profiles"),
    listingDefaults: () => request<{
      fulfillment_policy_id?: string; payment_policy_id?: string; return_policy_id?: string; merchant_location_key?: string;
    }>("/ebay/listing-defaults"),
    setListingDefaults: (defaults: {
      fulfillment_policy_id?: string; payment_policy_id?: string; return_policy_id?: string; merchant_location_key?: string;
    }) => request<{
      fulfillment_policy_id?: string; payment_policy_id?: string; return_policy_id?: string; merchant_location_key?: string;
    }>("/ebay/listing-defaults", { method: "PUT", body: JSON.stringify(defaults) }),
  },
  ebayOffers: {
    status: () => request<EbayOffersStatus>("/ebay-offers"),
    preview: (selections: EbayOfferSelection[], discountPercent: number) =>
      request<{ offers: EbayOffer[] }>("/ebay-offers/preview", {
        method: "POST", body: JSON.stringify({ selections, discount_percent: discountPercent }),
      }),
    send: (attemptIds: string[]) => request<{ offers: EbayOffer[] }>("/ebay-offers/send", {
      method: "POST", body: JSON.stringify({ attempt_ids: attemptIds, confirm: true }),
    }),
    enable: (attemptIds: string[]) => request<OkResponse>("/ebay-offers/enable", {
      method: "POST", body: JSON.stringify({ attempt_ids: attemptIds, confirm: true }),
    }),
    enableAll: (discountPercent: number, minimumPrice: string) => request<OkResponse>("/ebay-offers/enable-all", {
      method: "POST", body: JSON.stringify({ discount_percent: discountPercent, minimum_price: minimumPrice || null, confirm: true }),
    }),
    pause: (ruleId?: string) => request<OkResponse>(ruleId ? `/ebay-offers/rules/${ruleId}/pause` : "/ebay-offers/pause", { method: "POST" }),
    checked: (attemptId: string) => request<OkResponse>(`/ebay-offers/attempts/${attemptId}/checked`, {
      method: "POST", body: JSON.stringify({ confirm: true }),
    }),
  },
  directConnections: {
    status: () => request<DirectConnectionsStatus>("/marketplace-accounts"),
    connect: (marketplace: DirectMarketplace) =>
      request<{ authorizeUrl: string }>(`/marketplace-accounts/${marketplace}/connect`, { method: "POST" }),
    cancel: (marketplace: DirectMarketplace) =>
      request<OkResponse>(`/marketplace-accounts/${marketplace}/connect`, { method: "DELETE" }),
    disconnect: (marketplace: DirectMarketplace) =>
      request<OkResponse>(`/marketplace-accounts/${marketplace}`, { method: "DELETE" }),
  },
  marketplaceSessions: {
    status: () => request<ExtensionSessions>("/marketplaces/sessions"),
    connect: (marketplace: ExtensionSessionMarket) =>
      request<ExtensionSessionConnect>(`/marketplaces/${marketplace}/connect`, { method: "POST" }),
  },
  marketplaceListings: {
    get: (convId: string) => request<MarketplaceListings>(`/conversations/${convId}/marketplace-listings`),
    delist: (convId: string, marketplaces: DelistMarketplace[]) =>
      request<MarketplaceListings>(`/conversations/${convId}/marketplace-listings/delist`, {
        method: "POST", body: JSON.stringify({ marketplaces, confirm: true }),
      }),
    mark: (convId: string, marketplace: DelistMarketplace, state: MarketplaceMark, url?: string) =>
      request<MarketplaceListings>(`/conversations/${convId}/marketplace-listings/${marketplace}/mark`, {
        method: "POST", body: JSON.stringify({ state, ...(url ? { url } : {}) }),
      }),
    recordSale: (convId: string, sale: SellerSale) =>
      request<SellerSaleResult>(`/conversations/${convId}/marketplace-listings/sold`, {
        method: "POST", body: JSON.stringify(sale),
      }),
    checkFacebook: (convId: string) =>
      request<FacebookCheck>(`/conversations/${convId}/marketplace-listings/facebook/check`, { method: "POST" }),
    checkSales: (convId: string) =>
      request<SalesCheck>(`/conversations/${convId}/marketplace-listings/check-sales`, { method: "POST" }),
  },
  nativeMarketplaces: {
    sellwildConnect: (convId: string) =>
      request<OkResponse>(`/conversations/${convId}/native-marketplaces/sellwild/connect`, { method: "POST" }),
    vintedConnect: (convId: string) =>
      request<OkResponse>(`/conversations/${convId}/native-marketplaces/vinted/connect`, { method: "POST" }),
    vintedCategories: (convId: string) =>
      request<{ options: string[] }>(`/conversations/${convId}/native-marketplaces/vinted/categories`),
    vintedBrands: (convId: string, q: string, category: string) =>
      request<{ options: string[] }>(`/conversations/${convId}/native-marketplaces/vinted/brands?${new URLSearchParams({ q, category })}`),
    form: (convId: string, marketplace: string, revisionId?: string | null) =>
      hostedBrowser()?.form(convId, marketplace, revisionId) ?? request<MarketplaceForm>(`/conversations/${convId}/native-marketplaces/${marketplace}/form${revisionId ? `?revision_id=${encodeURIComponent(revisionId)}` : ""}`),
    status: (convId: string) => request<NativeDrafts>(`/conversations/${convId}/native-marketplaces`),
    preflight: (convId: string, sellwild?: { categoryId?: number; conditionId?: number }) => {
      const params = new URLSearchParams();
      if (sellwild?.categoryId) params.set("sellwild_category_id", String(sellwild.categoryId));
      if (sellwild?.conditionId) params.set("sellwild_condition_id", String(sellwild.conditionId));
      const query = params.toString();
      return request<NativePreflight>(`/conversations/${convId}/native-marketplaces/preflight${query ? `?${query}` : ""}`);
    },
    sellwildOptions: (convId: string) => request<SellwildOptions>(`/conversations/${convId}/native-marketplaces/sellwild/options`),
    saveDraft: (convId: string, marketplace: NativeMarketplace, revisionId: string) =>
      request<NativeDraft>(`/conversations/${convId}/native-marketplaces/${marketplace}/draft`, {
        method: "POST", body: JSON.stringify({ revision_id: revisionId }),
      }),
    list: (convId: string, marketplace: NativeMarketplace, revisionId: string, sellwild?: SellwildChoice) =>
      request<NativeDraft>(`/conversations/${convId}/native-marketplaces/${marketplace}/list`, {
        method: "POST", body: JSON.stringify({ revision_id: revisionId, confirm: true,
          ...(sellwild ? { sellwild_category_id: sellwild.categoryId, sellwild_condition_id: sellwild.conditionId } : {}) }),
      }),
    update: (convId: string, marketplace: NativeMarketplace, revisionId: string) =>
      request<NativeDraft>(`/conversations/${convId}/native-marketplaces/${marketplace}/update`, {
        method: "POST", body: JSON.stringify({ revision_id: revisionId, confirm: true }),
      }),
    cancel: (convId: string, marketplace: NativeMarketplace) =>
      request<NativeDraft>(`/conversations/${convId}/native-marketplaces/${marketplace}/cancel`, { method: "POST" }),
    reset: (convId: string, marketplace: NativeMarketplace) =>
      request<NativeDraft>(`/conversations/${convId}/native-marketplaces/${marketplace}/reset`, {
        method: "POST", body: JSON.stringify({ confirm: true }),
      }),
    check: (convId: string, marketplace: NativeMarketplace, draftUrl?: string) =>
      request<NativeDraft>(`/conversations/${convId}/native-marketplaces/${marketplace}/check`, {
        method: "POST", body: JSON.stringify(draftUrl ? { draft_url: draftUrl } : {}),
      }),
  },
  templates: {
    list: () => request<{templates: ListingTemplate[]}>("/listing-templates"),
    source: (id: string) => request<TemplateSource>(`/listing-templates/source/${id}`),
    save: (body: TemplateSave, id?: string) => request<ListingTemplate>(id ? `/listing-templates/${id}` : "/listing-templates", { method: id ? "PUT" : "POST", body: JSON.stringify(body) }),
    remove: (id: string, revision: number) => request<OkResponse>(`/listing-templates/${id}?expected_revision=${revision}`, {method:"DELETE"}),
    draft: (id: string, body: TemplateDraft) => request<{conversation_id:string}>(`/listing-templates/${id}/draft`, {method:"POST",body:JSON.stringify(body)}),
    draftResult: (id: string, requestId: string) => request<{conversation_id:string}>(`/listing-templates/${id}/draft-result/${requestId}`),
    preview: (id: string, body: TemplateApply) => request<TemplateReview>(`/listing-templates/${id}/apply-preview`, {method:"POST",body:JSON.stringify(body)}),
    apply: (id: string, body: TemplateApproval) => request<{ok:boolean;changed:boolean}>(`/listing-templates/${id}/apply`, {method:"POST",body:JSON.stringify(body)}),
  },
  bulkEdit: {
    preview: (conversationIds: string[], edits: BulkEdits) => request<{ rows: BulkReviewRow[] }>("/listings/bulk-edit/preview", { method: "POST", body: JSON.stringify({ conversation_ids: conversationIds, edits }) }),
    apply: (edits: BulkEdits, rows: BulkReviewToken[]) => request<BulkEditResult>("/listings/bulk-edit/apply", { method: "POST", body: JSON.stringify({ edits, rows }) }),
  },
  bulkMarketplace: {
    preview: (conversationIds: string[], action: BulkMarketplaceAction, marketplaces: DelistMarketplace[]) =>
      request<BulkMarketplacePreview>("/listings/marketplace-actions/preview", { method: "POST", body: JSON.stringify({ conversation_ids: conversationIds, action, marketplaces }) }),
    perform: (body: BulkMarketplacePerform) =>
      request<BulkMarketplaceResult>("/listings/marketplace-actions/perform", { method: "POST", body: JSON.stringify(body) }),
  },
  promotions: {
    history: () => request<PromotionHistory>("/promotions"),
    floors: (conversationIds: string[], marketplace: PromotionMarketplace) =>
      request<Record<string, PromotionFloor>>("/promotions/floors", { method: "POST", body: JSON.stringify({ conversation_ids: conversationIds, marketplace }) }),
    preview: (body: PromotionPreviewRequest) =>
      request<PromotionPreview>("/promotions/preview", { method: "POST", body: JSON.stringify(body) }),
    perform: (id: string, shippingDiscountId?: string) =>
      request<PromotionResult>(`/promotions/${encodeURIComponent(id)}/perform`, { method: "POST",
        body: JSON.stringify({ confirm: true, ...(shippingDiscountId ? { shipping_discount_id: shippingDiscountId } : {}) }) }),
    check: (id: string) => request<Promotion>(`/promotions/${encodeURIComponent(id)}/check`, { method: "POST" }),
    markChecked: (id: string) =>
      request<Promotion>(`/promotions/${encodeURIComponent(id)}/checked`, { method: "POST", body: JSON.stringify({ confirm: true }) }),
    saveFloors: (marketplace: PromotionMarketplace, floors: Record<string, string>) =>
      request<Record<string, PromotionFloor>>("/promotions/floors", { method: "PUT", body: JSON.stringify({ marketplace, floors }) }),
  },
  promotionAutomations: {
    list: () => request<PromotionAutomations>("/promotion-automations"),
    create: (body: PromotionAutomationBody) =>
      request<PromotionAutomation>("/promotion-automations", { method: "POST", body: JSON.stringify(body) }),
    update: (id: string, body: PromotionAutomationBody) =>
      request<PromotionAutomation>(`/promotion-automations/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),
    remove: (id: string) => request<{ ok: boolean }>(`/promotion-automations/${encodeURIComponent(id)}`, { method: "DELETE" }),
    enable: (id: string) =>
      request<PromotionAutomation>(`/promotion-automations/${encodeURIComponent(id)}/enable`, { method: "POST", body: JSON.stringify({ confirm: true }) }),
    pause: (id: string) => request<{ ok: boolean }>(`/promotion-automations/${encodeURIComponent(id)}/pause`, { method: "POST" }),
    pauseAll: () => request<{ paused: number }>("/promotion-automations/pause", { method: "POST" }),
    coverage: (id: string) => request<PromotionCoverage>(`/promotion-automations/${encodeURIComponent(id)}/coverage`),
    activity: (id: string) => request<PromotionActivity>(`/promotion-automations/${encodeURIComponent(id)}/activity`),
  },
  bulkDelete: {
    preview: (conversationIds: string[]) =>
      request<{ rows: BulkDeleteRow[] }>("/listings/bulk-delete/preview", { method: "POST", body: JSON.stringify({ conversation_ids: conversationIds }) }),
    delete: (rows: { conversation_id: string; review_token: string }[]) =>
      request<BulkDeleteResult>("/listings/bulk-delete/delete", { method: "POST", body: JSON.stringify({ rows, confirm: true }) }),
  },
  salesLedger: {
    list: (filters: SalesLedgerFilters, page = 1) => request<SalesLedgerPage>(`/sales-ledger?${salesLedgerParams(filters, page)}`),
    get: (saleId: string) => request<SalesLedgerRow>(`/sales-ledger/${encodeURIComponent(saleId)}`),
    save: (saleId: string, body: FinancialSave) => request<SalesLedgerRow>(`/sales-ledger/${encodeURIComponent(saleId)}/financials`, { method: "PUT", body: JSON.stringify(body) }),
    salesCsv: (filters: SalesLedgerFilters) => `${BASE}/sales-ledger/export/sales.csv?${salesLedgerParams(filters)}`,
    inventoryCsv: () => `${BASE}/sales-ledger/export/inventory.csv`,
  },
  marketplaceSales: {
    recover: (body: OrderRecoveryRequest) => request<OrderRecoveryResult>("/marketplace-sales/recover", { method: "POST", body: JSON.stringify(body) }),
    reconcile: (marketplace?: string) => request<{ matched: number; unmatched: number }>(
      `/marketplace-sales/reconcile${marketplace ? `?${new URLSearchParams({ marketplace })}` : ""}`, { method: "POST" },
    ),
    list: (options: { state?: MarketplaceSaleState; conversationId?: string } = {}) => {
      const params = new URLSearchParams({ state: options.state ?? "new" });
      if (options.conversationId) params.set("conversation_id", options.conversationId);
      return request<{ sales: MarketplaceSale[] }>(`/marketplace-sales?${params}`);
    },
    dismiss: (saleId: string) => request<OkResponse>(`/marketplace-sales/${saleId}/dismiss`, { method: "POST" }),
  },
  vendooApi: {
    listingFields: (convId: string) =>
      request<{ ok: boolean; forms: MarketplaceForm[] }>(
        `/conversations/${convId}/vendoo-api/fields`,
      ),
    create: (convId: string) =>
      request<{
        ok: boolean;
        job_id: string;
        item_id: string;
        url: string;
        unresolved: { marketplace?: string; field?: string; reason?: string }[];
        unfilled: { marketplace?: string; field?: string; reason?: string }[];
        diff: unknown[];
      }>(`/conversations/${convId}/vendoo-api/create`, { method: "POST" }),
    save: (convId: string) =>
      request<{ ok: boolean; item_id: string; updated: string[]; relist_needed: string[] }>(
        `/conversations/${convId}/vendoo-api/save`, { method: "POST" },
      ),
    relistDone: (convId: string) =>
      request<{ ok: boolean }>(
        `/conversations/${convId}/vendoo-api/relist-done`, { method: "POST" },
      ),
    sync: (convId: string, signal?: AbortSignal) =>
      request<{
        ok: boolean;
        action: "pull" | "conflict" | "label" | "none" | "unavailable";
        reason: string;
        item_id?: string;
        revision_id?: string;
        vendoo_status?: string;
      }>(
        `/conversations/${convId}/vendoo-api/sync`, { method: "POST", signal },
      ),
    syncStatus: (convId: string) =>
      request<VendooSyncStatus>(`/conversations/${convId}/vendoo-api/sync`),
    pull: (convId: string) =>
      request<{ ok: boolean; item_id: string; revision_id: string }>(
        `/conversations/${convId}/vendoo-api/pull`, { method: "POST" },
      ),
  },

  health: () => request<{ status: string; version: string }>("/health"),

  status: () =>
    request<{
      version: string;
      provider_configured: boolean;
      extension_connected: boolean;
      active_job_id: string | null;
      packaged?: boolean;
      chrome_available?: boolean;
      conversations?: number;
      setup_guide_dismissed?: boolean;
      data_dir?: string;
    }>("/status"),

  conversations: {
    copyBatchPreview: (ids: string[]) => request<{ rows: BatchCopyPreviewRow[] }>("/listings/copies/preview", { method: "POST", body: JSON.stringify({ conversation_ids: ids }) }),
    copyResult: (id: string, requestId: string) => request<Conversation>(`/conversations/${id}/copy-result/${requestId}`),
    copyPreview: (id: string) => request<CopyPreview>(`/conversations/${id}/copy-preview`),
    copy: (id: string, body: CopyListingRequest) => request<Conversation>(`/conversations/${id}/copy`, { method: "POST", body: JSON.stringify(body) }),
    duplicateVendooLinks: () => request<{
      vendoo_item_id: string;
      vendoo_url: string;
      listings: { id: string; title: string }[];
    }[]>("/conversations/vendoo-link-duplicates"),
    list: () => request<ConversationSummary[]>("/conversations"),
    get: (id: string) => request<Conversation>(`/conversations/${id}`),
    create: (body?: { title?: string; notes?: string; box_id?: string | null }) =>
      request<Conversation>("/conversations", { method: "POST", body: JSON.stringify(body || {}) }),
    // Status is not here on purpose: it follows the bound Vendoo item.
    update: (id: string, body: { title?: string; notes?: string; box_id?: string | null }) =>
      request<Conversation>(`/conversations/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    settle: (id: string) =>
      request<Conversation>(`/conversations/${id}/settle`, { method: "POST" }),
    unsettle: (id: string) =>
      request<Conversation>(`/conversations/${id}/unsettle`, { method: "POST" }),
    linkVendoo: (id: string, urlOrId: string) =>
      request<{
        conversation: Conversation;
        vendoo_item_id: string;
        vendoo_url: string;
      }>(`/conversations/${id}/vendoo-link`, {
        method: "POST",
        body: JSON.stringify({ url_or_id: urlOrId }),
      }),
    stop: (id: string) =>
      request<{ ok: boolean }>(`/conversations/${id}/stop`, { method: "POST" }),
    activity: (id: string) => request<ConversationActivity>(`/conversations/${id}/activity`),
    messages: (id: string) => request<Message[]>(`/conversations/${id}/messages`),
    /** The item's photos, or with `marketplace` the photos added on that marketplace's form only. */
    photos: (id: string, marketplace?: string) =>
      request<Photo[]>(`/conversations/${id}/photos${marketplace ? `?marketplace=${encodeURIComponent(marketplace)}` : ""}`),
    deletePhoto: (convId: string, photoId: string) =>
      request<OkResponse>(`/conversations/${convId}/photos/${photoId}`, { method: "DELETE" }),
    /** Rotate the stored image clockwise by `degrees` (90, -90 or 180). */
    rotatePhoto: (convId: string, photoId: string, degrees: 90 | -90 | 180) =>
      request<Photo>(`/conversations/${convId}/photos/${photoId}/rotate`, {
        method: "POST",
        body: JSON.stringify({ degrees }),
      }),
    /** Replace a photo's edits; the server renders them from the kept original. */
    editPhoto: (convId: string, photoId: string, edits: PhotoEdits) =>
      request<Photo>(`/conversations/${convId}/photos/${photoId}/edits`, {
        method: "PUT",
        body: JSON.stringify(edits),
      }),
    resetPhoto: (convId: string, photoId: string) =>
      request<Photo>(`/conversations/${convId}/photos/${photoId}/edits`, { method: "DELETE" }),
    /** A smaller JPEG of unsaved edits; `crop: false` keeps the whole frame to draw the crop on. */
    previewPhotoEdits: async (convId: string, photoId: string, edits: PhotoEdits, crop: boolean, signal?: AbortSignal) => {
      const res = await studioFetch(`${BASE}/conversations/${convId}/photos/${photoId}/edits/preview?crop=${crop}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(edits),
        signal,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(errorMessage(body, "Preview failed"));
      }
      return res.blob();
    },
    delete: (id: string) =>
      request<DeleteConversationResult>(`/conversations/${id}`, { method: "DELETE" }),
    reset: (id: string, opts?: { keepInputs?: boolean }) =>
      requestWithTimeout<Conversation>(`/conversations/${id}/reset`, {
        method: "POST",
        body: JSON.stringify({ keep_inputs: Boolean(opts?.keepInputs) }),
      }, "Resetting the listing took too long. Check the listing before trying Regenerate again; you do not need to restart Anteroom."),
    reorderPhotos: (convId: string, orderedIds: string[]) =>
      request<OkResponse>(`/conversations/${convId}/photos/order`, {
        method: "PATCH",
        body: JSON.stringify(orderedIds),
      }),
    uploadPhotos: (convId: string, files: File[], marketplace?: string): Promise<PhotoUploadResult> => {
      if (hasHostedApiSession()) return (async () => {
        const ids: string[] = []; const errors: string[] = [];
        for (const file of files) {
          if (file.size > 10 * 1024 * 1024) { errors.push(`${file.name}: choose a photo smaller than 10 MB.`); continue; }
          const query = marketplace ? `?marketplace=${encodeURIComponent(marketplace)}` : "";
          const response = await studioFetch(`${BASE}/drafts/${encodeURIComponent(convId)}/photos${query}`, { method: "POST", body: file });
          if (!response.ok) { errors.push(`${file.name}: could not upload this photo.`); continue; }
          ids.push((await response.json()).id);
        }
        const photos = await request<Photo[]>(`/conversations/${encodeURIComponent(convId)}/photos${marketplace ? `?marketplace=${encodeURIComponent(marketplace)}` : ""}`);
        return { ok: errors.length === 0, count: ids.length, photos: photos.filter(photo => ids.includes(photo.id)), errors };
      })();
      const form = new FormData();
      files.forEach((f) => form.append("files", f));
      if (marketplace) form.append("marketplace", marketplace);
      return studioFetch(`${BASE}/conversations/${convId}/photos`, { method: "POST", body: form }).then(
        async (r) => {
          const data = await r.json();
          if (!r.ok) {
            throw new Error(data.detail || `Upload failed: ${r.status}`);
          }
          return data;
        }
      );
    },
  },

  imports: {
    bulkStatus: () => request<VendooBulkImport>("/imports/vendoo/bulk"),
    startBulk: () => request<VendooBulkImport>("/imports/vendoo/bulk", { method: "POST" }),
    cancelBulk: () =>
      request<VendooBulkImport>("/imports/vendoo/bulk/cancel", { method: "POST" }),
    syncLabels: () => request<VendooLabelSync>("/imports/vendoo/labels", { method: "POST" }),
    labelSyncStatus: () => request<VendooLabelSync>("/imports/vendoo/labels"),
    labelNames: () => request<VendooLabelConversion>("/imports/vendoo/label-names"),
    convertLabelNames: () => request<VendooLabelConversion>("/imports/vendoo/label-names", { method: "POST" }),
  },

  similarItems: {
    list: () => request<SimilarItems>("/similar-items"),
    dismiss: (firstId: string, secondId: string) =>
      request<{ ok: boolean }>("/similar-items/dismiss", { method: "POST", body: JSON.stringify({ first_id: firstId, second_id: secondId }) }),
    merge: (keepId: string, mergeId: string) =>
      request<SimilarMerge>("/similar-items/merge", { method: "POST", body: JSON.stringify({ keep_id: keepId, merge_id: mergeId }) }),
  },

  listings: {
    get: (convId: string) => request<ListingResponse>(`/conversations/${convId}/listing`),
    update: (convId: string, listing: ListingData, expectedRevisionId: string) =>
      request<ListingUpdateResult>(`/conversations/${convId}/listing`, {
        method: "PUT",
        body: JSON.stringify({ listing, expected_revision_id: expectedRevisionId }),
      }),
    validate: (convId: string) =>
      request<ValidationResult>(`/conversations/${convId}/listing/validate`, { method: "POST" }),
    revisions: (convId: string) => request<ListingRevision[]>(`/conversations/${convId}/revisions`),
    revision: (convId: string, revisionId: string) =>
      request<{ id: string; listing: ListingData }>(`/conversations/${convId}/revisions/${revisionId}`),
    restore: (convId: string, revisionId: string, expectedRevisionId: string) =>
      request<RevisionRestoreResult>(`/conversations/${convId}/revisions/${revisionId}/restore`, {
        method: "POST", body: JSON.stringify({ expected_revision_id: expectedRevisionId }),
      }),
    priceDropPreview: (convId: string) =>
      request<PriceDropPreview>(`/conversations/${convId}/price-drop/preview`, { method: "POST" }),
    /** Stream sold comps: each source's progress, then the preview they imply. */
    priceDropComps: async (
      convId: string,
      handlers: {
        onSource: (source: PriceDropCompsSource) => void;
        onStep: (text: string) => void;
        onPreview: (preview: PriceDropPreview) => void;
      },
      signal?: AbortSignal,
    ): Promise<void> => {
      const res = await studioFetch(`${BASE}/conversations/${convId}/price-drop/comps`, {
        method: "POST",
        headers: { Accept: "text/event-stream" },
        cache: "no-store",
        signal,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(errorMessage(body, `Request failed: ${res.status}`));
      }
      let failure = "";
      await readSse(res, (event, data) => {
        if (event === "source") handlers.onSource(JSON.parse(data) as PriceDropCompsSource);
        else if (event === "step") handlers.onStep(data);
        else if (event === "preview") handlers.onPreview(JSON.parse(data) as PriceDropPreview);
        else if (event === "error") failure = data;
      });
      if (failure) throw new Error(failure);
    },
    applyPriceDrop: (
      convId: string,
      body: { price: number; percent?: number | null; mode: "percent" | "comps" | "custom" },
    ) =>
      requestWithTimeout<PriceDropApplyResult>(`/conversations/${convId}/price-drop`, {
        method: "POST",
        body: JSON.stringify(body),
      }, "Saving the price took too long. Check the listing's price before confirming again; you do not need to restart Anteroom."),
    /** A suggested markdown for each listing, from the seller's own sales. Changes nothing. */
    markdownPreview: (conversationIds: string[]) =>
      request<{ rows: MarkdownPreviewRow[] }>("/price-drop/batch/preview", {
        method: "POST",
        body: JSON.stringify({ conversation_ids: conversationIds }),
      }),
    /** Save confirmed prices as Studio revisions. Never sends to Vendoo. */
    applyMarkdowns: (drops: MarkdownDropRequest[]) =>
      requestWithTimeout<MarkdownApplyResult>("/price-drop/batch", {
        method: "POST",
        body: JSON.stringify({ drops }),
      }, "Saving the prices took too long. Check the listings' prices before applying again; you do not need to restart Anteroom."),
  },

  jobs: {
    send: async (conversationId: string) => {
      // The button click approves the send. Freeze its preparation before queueing.
      const prepared = await request<SendPreview>("/jobs/send-preview", {
        method: "POST", body: JSON.stringify({ conversation_id: conversationId }),
      });
      return request<Job>("/jobs/send", {
        method: "POST", body: JSON.stringify({ conversation_id: conversationId, review_id: prepared.review_id }),
      });
    },
    queue: () => request<{
      work: { conversation_id: string; title: string; detail: string }[];
      jobs: Job[];
    }>("/jobs/queue"),
    list: (conversationId?: string) => {
      const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : "";
      return request<Job[]>(`/jobs${query}`);
    },
    ensureDraft: (conversationId: string) =>
      request<Job>("/jobs/ensure-draft", {
        method: "POST",
        body: JSON.stringify({ conversation_id: conversationId }),
      }),
    get: (id: string) => request<Job>(`/jobs/${id}`),
    importDraft: (id: string) =>
      request<{ ok: boolean; listing_title: string; photo_count: number; photo_warnings: string[] }>(
        `/jobs/${id}/import-draft`,
        { method: "POST" },
      ),
    fillLog: (id: string) => request<FillLogReport>(`/jobs/${id}/fill-log`),
    marketplaceStatuses: (ids: string[]) =>
      request<Record<string, Record<string, unknown>>>(
        `/jobs/marketplace-statuses?job_ids=${encodeURIComponent(ids.join(","))}`,
      ),
    vendooItem: (
      id: string,
      opts?: { refresh?: boolean; cacheOnly?: boolean; resolvePhotos?: boolean; signal?: AbortSignal },
    ) => {
      const params = new URLSearchParams();
      if (opts?.refresh) params.set("refresh", "true");
      if (opts?.cacheOnly) params.set("cache_only", "true");
      if (opts?.resolvePhotos) params.set("resolve_photos", "true");
      const query = params.toString();
      return request<VendooItemResult>(`/jobs/${id}/vendoo-item${query ? `?${query}` : ""}`, { method: "POST", signal: opts?.signal });
    },
    resolveCategory: (id: string, query?: string) =>
      request<{ ok: boolean; query?: string; path?: string; matches?: { text?: string; path?: string; score?: number }[]; error?: string }>(
        `/jobs/${id}/resolve-category`,
        {
          method: "POST",
          body: JSON.stringify({ query: query || null }),
        },
      ),
    open: (id: string) =>
      requestWithTimeout<{ ok: boolean; url: string; via: "extension" | "chrome" }>(
        `/jobs/${id}/open`, { method: "POST" },
        "Opening the listing took too long. Try Open listing again; you do not need to restart Anteroom.",
        25000,
      ),
    browser: {
      open: (id: string) =>
        request<{ ok: boolean; controller: string; input?: boolean; warning?: string }>(`/jobs/${id}/browser/open`, {
          method: "POST",
          body: JSON.stringify({}),
        }),
      close: (id: string) => request<{ ok: boolean }>(`/jobs/${id}/browser/close`, { method: "POST" }),
      input: (id: string, events: BrowserInputEvent[]) =>
        request<{ sent: boolean }>(`/jobs/${id}/browser/input`, {
          method: "POST",
          body: JSON.stringify({ events }),
        }),
      viewport: (id: string, viewport: { width: number; height: number; scale: number }) =>
        request<{ sent: boolean }>(`/jobs/${id}/browser/viewport`, {
          method: "POST",
          body: JSON.stringify(viewport),
        }),
      navigate: (id: string, direction: "back" | "forward") =>
        request<{ ok: boolean }>(`/jobs/${id}/browser/navigate`, {
          method: "POST", body: JSON.stringify({ direction }),
        }),
      pick: (id: string, xRatio: number, yRatio: number) =>
        request<{ ok: boolean; viewport: BrowserViewport; field: BrowserField | null; element: { text: string; danger: boolean } | null }>(
          `/jobs/${id}/browser/pick`,
          { method: "POST", body: JSON.stringify({ x_ratio: xRatio, y_ratio: yRatio }) },
        ),
      snapshot: (id: string) =>
        request<{ ok: boolean; url: string; viewport: BrowserViewport; fields: BrowserField[] }>(`/jobs/${id}/browser/snapshot`),
    },
    cancel: (id: string) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  },

  settings: {
    saleDetection: () => request<SaleDetectionStatus>("/settings/sale-detection"),
    setSaleDetection: (marketplace: string, enabled: boolean) =>
      request<SaleDetectionStatus>("/settings/sale-detection", {
        method: "PUT",
        body: JSON.stringify({ marketplace, enabled }),
      }),
    setSaleAutodelist: (autodelist: boolean) =>
      request<SaleDetectionStatus>("/settings/sale-detection", {
        method: "PUT",
        body: JSON.stringify({ autodelist }),
      }),
    provider: () => request<ProviderStatus>("/settings/provider"),
    setProvider: (apiKey: string) =>
      request<OkResponse>("/settings/provider", {
        method: "PUT",
        body: JSON.stringify({ api_key: apiKey }),
      }),
    deleteKey: () => request<OkResponse>("/settings/provider/key", { method: "DELETE" }),
    testConnection: () => request<ProviderTestResult>("/settings/provider/test", { method: "POST" }),
    testMimo: () =>
      request<ProviderTestResult>("/settings/provider/mimo/test", { method: "POST" }),
    setPreferredProvider: (order: {
      primary: ListingProviderId;
      fallback?: ListingProviderId | "none" | null;
    }) =>
      request<{ ok: boolean; primary: ListingProviderId; fallback: ListingProviderId | "none" }>(
        "/settings/provider/preferred",
        {
          method: "PUT",
          body: JSON.stringify(order),
        },
      ),
    setPhotoProvider: (choice: PhotoProviderChoice) =>
      request<{ ok: boolean; photo_provider: PhotoProviderChoice }>("/settings/provider/photos", {
        method: "PUT",
        body: JSON.stringify({ choice }),
      }),
    brave: () => request<{ configured: boolean; masked_key: string | null }>("/settings/brave"),
    setBrave: (apiKey: string) =>
      request<{ ok: boolean; configured: boolean; masked_key: string | null }>("/settings/brave", {
        method: "PUT",
        body: JSON.stringify({ api_key: apiKey }),
      }),
    deleteBrave: () =>
      request<{ ok: boolean; configured: boolean; masked_key: string | null }>("/settings/brave", {
        method: "DELETE",
      }),
    testBrave: () => request<{ ok: boolean; error?: string | null }>("/settings/brave/test", { method: "POST" }),
    cursor: () => request<{ configured: boolean; masked_key: string | null }>("/settings/cursor"),
    setCursor: (apiKey: string) =>
      request<{ ok: boolean; configured: boolean; masked_key: string | null }>("/settings/cursor", {
        method: "PUT",
        body: JSON.stringify({ api_key: apiKey }),
      }),
    deleteCursor: () =>
      request<{ ok: boolean; configured: boolean; masked_key: string | null }>("/settings/cursor", {
        method: "DELETE",
      }),
    testCursor: () => request<{ ok: boolean; error?: string | null }>("/settings/cursor/test", { method: "POST" }),
    aiKeys: () => request<{ providers: AIKeyProvider[] }>("/settings/ai-keys"),
    setAIKey: (id: KeyProviderId, apiKey: string) =>
      request<{ ok: boolean }>(`/settings/ai-keys/${id}`, {
        method: "PUT",
        body: JSON.stringify({ api_key: apiKey }),
      }),
    deleteAIKey: (id: KeyProviderId) => request<{ ok: boolean }>(`/settings/ai-keys/${id}`, { method: "DELETE" }),
    testAIKey: (id: KeyProviderId) =>
      request<{ ok: boolean; error?: string | null }>(`/settings/ai-keys/${id}/test`, { method: "POST" }),
    aiKeyModels: (id: KeyProviderId) => request<AIKeyModels>(`/settings/ai-keys/${id}/models`),
    setAIKeyModels: (
      id: KeyProviderId,
      models: { vision_model?: string; listing_model?: string; reasoning_effort?: string },
    ) =>
      request<{ ok: boolean; vision_model?: string; listing_model?: string }>(`/settings/ai-keys/${id}/models`, {
        method: "PUT",
        body: JSON.stringify(models),
      }),
    openRouterSignIn: () => request<{ authorize_url: string }>("/settings/ai-keys/openrouter/sign-in", { method: "POST" }),
    chatgptLogin: () => request<ChatGPTPendingLogin>("/settings/chatgpt/login", { method: "POST" }),
    chatgptCancelLogin: () => request<OkResponse>("/settings/chatgpt/login", { method: "DELETE" }),
    chatgptLogout: () => request<OkResponse>("/settings/chatgpt", { method: "DELETE" }),
    /** WebSocket that runs `claude auth login` for the sign-in terminal. */
    claudeLoginTerminalUrl: (cols: number, rows: number) => {
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      return `${scheme}://${window.location.host}${BASE}/settings/claude/terminal?cols=${cols}&rows=${rows}`;
    },
    /** Each provider's subscription usage. `refresh` skips the server's short cache. */
    usageLimits: (refresh = false) =>
      request<{ ok: boolean; providers: ProviderUsageLimits[] }>(
        `/settings/usage-limits${refresh ? "?refresh=1" : ""}`,
      ),
    claudeModels: () =>
      request<{
        models: { value: string; label: string; efforts: string[] }[];
        vision_model: string;
        listing_model: string;
        effort: string | null;
        efforts: string[];
        error?: string | null;
      }>("/settings/claude/models"),
    setClaudeModels: (models: { vision_model?: string; listing_model?: string; effort?: string }) =>
      request<OkResponse>("/settings/claude/models", {
        method: "PUT",
        body: JSON.stringify(models),
      }),
    chatgptModels: () =>
      request<{
        models: string[];
        vision_model: string;
        listing_model: string;
        reasoning_effort: string;
        reasoning_efforts: string[];
        error?: string | null;
      }>("/settings/chatgpt/models"),
    setChatGPTModels: (models: { vision_model?: string; listing_model?: string; reasoning_effort?: string }) =>
      request<{ ok: boolean; vision_model: string; listing_model: string; reasoning_effort: string }>(
        "/settings/chatgpt/models",
        {
          method: "PUT",
          body: JSON.stringify(models),
        },
      ),
    cursorModels: () =>
      request<{
        models: string[];
        /** Cursor's display name for each model id, where the catalog gives one. */
        labels?: Record<string, string>;
        vision_model: string;
        listing_model: string;
        reasoning: { param: string; options: { value: string; label: string }[]; value: string } | null;
        error?: string | null;
      }>("/settings/cursor/models"),
    setCursorModels: (models: { vision_model?: string; listing_model?: string; reasoning_effort?: string }) =>
      request<{ ok: boolean; vision_model: string; listing_model: string }>("/settings/cursor/models", {
        method: "PUT",
        body: JSON.stringify(models),
      }),
    marketplaces: () =>
      request<{
        available: { id: string; label: string; fillable: boolean }[];
        selected: string[];
        fillable: string[];
      }>("/settings/marketplaces"),
    setMarketplaces: (selected: string[]) =>
      request<{
        ok: boolean;
        available: { id: string; label: string; fillable: boolean }[];
        selected: string[];
        fillable: string[];
      }>("/settings/marketplaces", {
        method: "PUT",
        body: JSON.stringify({ selected }),
      }),
    packageDimensions: () =>
      request<{ length: number; width: number; height: number }>("/settings/package-dimensions"),
    setPackageDimensions: (dimensions: { length: number; width: number; height: number }) =>
      request<{ ok: boolean; length: number; width: number; height: number }>("/settings/package-dimensions", {
        method: "PUT",
        body: JSON.stringify(dimensions),
      }),
    shipsFromZip: () => request<{ zip: string }>("/settings/ships-from-zip"),
    setShipsFromZip: (zip: string) =>
      request<{ ok: boolean; zip: string }>("/settings/ships-from-zip", {
        method: "PUT",
        body: JSON.stringify({ zip }),
      }),
    listingDefaults: () =>
      request<{ defaults: Record<string, { value: string; label: string }> }>("/settings/listing-defaults"),
    setListingDefault: (body: { path: string; value: string; label?: string }) =>
      request<{ ok: boolean; defaults: Record<string, { value: string; label: string }> }>(
        "/settings/listing-defaults",
        { method: "PUT", body: JSON.stringify(body) },
      ),
    clearListingDefault: (path: string) =>
      request<{ ok: boolean; defaults: Record<string, { value: string; label: string }> }>(
        `/settings/listing-defaults?path=${encodeURIComponent(path)}`,
        { method: "DELETE" },
      ),
    ebayInventoryLocation: () => request<{ location: string }>("/settings/ebay-inventory-location"),
    setEbayInventoryLocation: (location: string) =>
      request<{ ok: boolean; location: string }>("/settings/ebay-inventory-location", {
        method: "PUT",
        body: JSON.stringify({ location }),
      }),
    hiddenFields: (conversationId?: string) =>
      request<{
        always: { marketplace: string; field: string; label: string }[];
        listing: { marketplace: string; field: string; label: string }[];
      }>(`/settings/hidden-fields${conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ""}`),
    hideField: (body: {
      marketplace: string;
      field: string;
      label?: string;
      scope: "always" | "listing";
      conversation_id?: string;
    }) =>
      request<{
        ok: boolean;
        always: { marketplace: string; field: string; label: string }[];
        listing: { marketplace: string; field: string; label: string }[];
      }>("/settings/hidden-fields", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    showField: (body: {
      marketplace: string;
      field: string;
      scope: "always" | "listing";
      conversation_id?: string;
    }) =>
      request<{
        ok: boolean;
        always: { marketplace: string; field: string; label: string }[];
        listing: { marketplace: string; field: string; label: string }[];
      }>("/settings/hidden-fields", {
        method: "DELETE",
        body: JSON.stringify(body),
      }),
    restoreAllHiddenFields: (conversationId?: string) =>
      request<{
        ok: boolean;
        always: { marketplace: string; field: string; label: string }[];
        listing: { marketplace: string; field: string; label: string }[];
      }>("/settings/hidden-fields/restore-all", {
        method: "POST",
        body: JSON.stringify({ conversation_id: conversationId }),
      }),
    restoreMatchingHiddenFields: (
      fields: { marketplace: string; field: string }[],
      conversationId?: string,
    ) =>
      request<{
        ok: boolean;
        always: { marketplace: string; field: string; label: string }[];
        listing: { marketplace: string; field: string; label: string }[];
      }>("/settings/hidden-fields/restore-matching", {
        method: "POST",
        body: JSON.stringify({ fields, conversation_id: conversationId }),
      }),
    dismissSetupGuide: () =>
      request<{ ok: boolean; dismissed: boolean }>("/settings/setup-guide/dismiss", { method: "POST" }),
    dataFolder: () =>
      request<{ path: string; contains: string[]; secrets: string }>("/settings/data-folder"),
    database: () => request<DatabaseReport>("/settings/database"),
    pruneDatabase: () =>
      request<DatabaseReport>("/settings/database/prune", { method: "POST" }),
    ui: () =>
      request<{
        ok: boolean;
        recent_vendoo_labels: string[];
        settled_shelf_expanded: boolean;
        hidden_vendoo_labels: string[];
        theme: "dark" | "light" | "system";
        listing_names: ListingNames;
        model_favorites: ModelFavorite[];
        vendoo_enabled: boolean;
        stale_listing_days: number;
        label_colors: Record<string, LabelColor>;
      }>("/settings/ui"),
    setUi: (body: {
      recent_vendoo_labels?: string[];
      settled_shelf_expanded?: boolean;
      theme?: "dark" | "light" | "system";
      listing_names?: ListingNames;
      remember_labels?: string | string[];
      restore_labels?: string[];
      forget_label?: string;
      model_favorites?: ModelFavorite[];
      vendoo_enabled?: boolean;
      stale_listing_days?: number;
      label_colors?: Record<string, LabelColor>;
    }) =>
      request<{
        ok: boolean;
        recent_vendoo_labels: string[];
        settled_shelf_expanded: boolean;
        hidden_vendoo_labels: string[];
        theme: "dark" | "light" | "system";
        listing_names: ListingNames;
        model_favorites: ModelFavorite[];
        vendoo_enabled: boolean;
        stale_listing_days: number;
        label_colors: Record<string, LabelColor>;
      }>("/settings/ui", { method: "PUT", body: JSON.stringify(body) }),
    formulas: () =>
      request<{
        ok: boolean;
        title: string;
        description: string;
        default_title: string;
        default_description: string;
      }>("/settings/formulas"),
    setFormulas: (body: { title?: string | null; description?: string | null }) =>
      request<{
        ok: boolean;
        title: string;
        description: string;
        default_title: string;
        default_description: string;
      }>("/settings/formulas", { method: "PUT", body: JSON.stringify(body) }),
    agent: () => request<AgentConnectionStatus>("/settings/agent"),
    testAgent: () => request<AgentConnectionTest>("/settings/agent/test", { method: "POST" }),
    tailscale: () =>
      request<{
        available: boolean;
        installed: boolean;
        enabled: boolean;
        https_port: number;
        target: string;
        dns_name: string | null;
        url: string | null;
        state: string;
        error: string | null;
        funnel: boolean;
      }>("/settings/tailscale"),
    enableTailscale: () =>
      request<{
        available: boolean;
        installed: boolean;
        enabled: boolean;
        https_port: number;
        target: string;
        dns_name: string | null;
        url: string | null;
        state: string;
        error: string | null;
        funnel: boolean;
      }>("/settings/tailscale/enable", { method: "POST" }),
    disableTailscale: () =>
      request<{
        available: boolean;
        installed: boolean;
        enabled: boolean;
        https_port: number;
        target: string;
        dns_name: string | null;
        url: string | null;
        state: string;
        error: string | null;
        funnel: boolean;
      }>("/settings/tailscale/disable", { method: "POST" }),
    vendooApiLogs: () => request<{ entries: VendooApiLogEntry[] }>("/settings/vendoo-api-logs"),
    vendooApiLog: (id: number) => request<VendooApiLogDetail>(`/settings/vendoo-api-logs/${id}`),
    clearVendooApiLogs: () => request<{ ok: boolean }>("/settings/vendoo-api-logs", { method: "DELETE" }),
    errorLogs: () => request<{ entries: StudioErrorLogEntry[] }>("/settings/error-logs"),
    clearErrorLogs: () => request<{ ok: boolean }>("/settings/error-logs", { method: "DELETE" }),
  },

  extension: {
    status: () =>
      request<{
        connected: boolean;
        paired: boolean;
        version?: string | null;
        expected_version?: string | null;
        expected_build?: string | null;
        build?: string | null;
        up_to_date?: boolean;
        reload_pending?: boolean;
        files_in_sync?: boolean;
        version_mismatch?: boolean;
        load_path?: string | null;
        duplicate_bridge?: boolean;
      }>("/extension/status"),
    pairingToken: () => request<{ token: string }>("/extension/pairing-token"),
    reload: () => request<{ ok: boolean; sent: boolean }>("/extension/reload", { method: "POST" }),
  },

  desktop: {
    chrome: () =>
      request<{
        available: boolean;
        browser: string | null;
        extension_dir: string;
        profile?: string;
        profile_dir?: string;
      }>("/desktop/chrome"),
    connectChrome: () =>
      request<{
        ok: boolean;
        connected?: boolean;
        extension_reload?: boolean;
        via?: string;
      }>("/desktop/chrome/connect", { method: "POST" }),
  },

  updates: {
    status: () =>
      request<{
        available: boolean;
        packaged?: boolean;
        behind?: number;
        ahead?: number;
        branch?: string;
        local_sha?: string;
        remote_sha?: string;
        remote_ref?: string;
        summary?: string;
        commits?: string[];
        short_sha?: string;
        on_ref?: boolean;
        dirty?: string[];
        error?: string | null;
      }>("/updates"),
    progress: () =>
      request<{
        status: "idle" | "downloading" | "downloaded" | "installing" | "error";
        download_percent: number | null;
        sha: string | null;
        error: string | null;
      }>("/updates/progress"),
    download: () =>
      request<{ ok: boolean; updated: boolean; prepared?: boolean; sha?: string }>("/updates/download", {
        method: "POST",
      }),
    restart: () =>
      request<{ ok: boolean; updated: boolean; sha?: string; reloading?: boolean }>("/updates/restart", {
        method: "POST",
      }),
    apply: () =>
      request<{ ok: boolean; updated: boolean; sha?: string; reloading?: boolean }>("/updates/apply", {
        method: "POST",
      }),
  },

  changelog: () =>
    request<{
      version: string;
      entries: { version: string; date: string | null; body: string }[];
    }>("/changelog"),
};
