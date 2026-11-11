import { listingScreenRead } from "./listingScreen";
import { reportError } from "./errorReports";
import type { VendooDraft } from "./vendooDraft";
import { ApiRequestError, retryAfterMs, studioFetch, hasHostedApiSession } from "./transport";
import { trackPhotoUpload } from "../photoUploads";
import type { ListingTemplate, TemplateSource, TemplateSave, TemplateDraft, TemplateApply, TemplateReview, TemplateApproval } from "./listingTemplates";
import type { CopyPreview, CopyListingRequest, BatchCopyPreviewRow } from "./listingCopy";
import type { ListingVideo, ListingVideoLibrary, MarketplaceMediaRules } from "./listingVideos";
import type { BulkEdits, BulkReviewRow, BulkReviewToken, BulkEditResult } from "./bulkEdit";
import type { BulkDeleteResult, BulkDeleteRow } from "./bulkDelete";
import type { LabelColor } from "../labelColors";
import type { BulkMarketplaceAction, BulkMarketplacePerform, BulkMarketplacePreview, BulkMarketplaceResult } from "./bulkMarketplace";
import { salesLedgerParams, salesOverviewParams, type FinancialSave, type SalesOverview, type SalesLedgerFilters, type SalesLedgerPage, type SalesLedgerRow } from "./salesLedger";
import type { OrderRecoveryRequest, OrderRecoveryResult } from "./orderRecovery";
import type {
  Promotion, PromotionActivity, PromotionAutomation, PromotionAutomationBody, PromotionAutomations, PromotionCoverage, PromotionFloor,
  PromotionHistory, PromotionMarketplace, PromotionPreview, PromotionPreviewRequest, PromotionResult,
} from "./promotions";
import type {
  BackupSnapshot,
  BackupsStatus,
  ChoiceList,
  Conversation,
  ConversationInput,
  ConversationSummary,
  DatabaseReport,
  DeleteConversationResult,
  Job,
  ErrorDetail,
  HTTPValidationError,
  ListingData,
  ListingSave,
  ListingNames,
  ChatAttachmentUpload,
  ListingProviderId,
  AIKeyModels,
  AIKeyProvider,
  KeyProviderId,
  ModelFavorite,
  ProviderUsageLimits,
  PhotoProviderChoice,
  ListingDocument,
  ListingRevision,
  ListingSaveResult,
  ConversationActivity,
  MarkdownApplyResult,
  MarkdownDropRequest,
  MarkdownPreviewRow,
  Message,
  OkResponse,
  ListingPhoto,
  PhotoEdits,
  PhotoUploadResult,
  PriceDropApplyResult,
  PriceDropCompsSource,
  PriceDropPreview,
  ProviderStatus,
  ProviderTestResult,
  StudioErrorLogEntry,
  ChatGPTPendingLogin,
  RevisionRestoreResult,
  ListingValidation,
  SimilarItems,
  SimilarMerge,
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
import type { DelistMarketplace, MarketplaceListings, MarketplaceMark, SalesCheck, SellerSale, SellerSaleResult } from "./marketplaceListings";
import type { ListingReadiness, NativeDraft, NativeDrafts, NativeMarketplace, NativePreflight } from "./nativeMarketplaces";
import type { ExtensionSessionConnect, ExtensionSessionMarket, ExtensionSessions } from "./marketplaceSessions";
import type { EbayOffer, EbayOfferSelection, EbayOffersStatus } from "./ebayOffers";
import type { EbayListing, EbayPageData, EbayPayload, EbayResult, EbayReview } from "./ebay";
import type { ChromeImportMarketplace, MarketplaceImportPage, MarketplaceImportResult } from "./inventoryImport";
import type { EtsyListing, EtsyListingState, EtsyOrders, EtsyOrderStatus, EtsyPage, EtsyShop } from "./etsy";

const BASE = "/api";

/** The contract's error answers (ErrorDetail, and FastAPI's HTTPValidationError for a 422), or
 * another route's own detail object. */
type ErrorBody = ErrorDetail | HTTPValidationError | { detail?: unknown; message?: unknown } | null | undefined;
type ErrorItem = { msg?: string; message?: string } | string | null | undefined;

export function errorMessage(body: ErrorBody, fallback: string): string {
  const detail: unknown = body?.detail;
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
  const message = body && "message" in body ? body.message : undefined;
  if (typeof message === "string" && message.trim()) return message;
  return fallback;
}

/** What a plain read accepts: React Query's signal, so a cancelled query stops its request. */
export interface ReadOptions { signal?: AbortSignal }

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  if (!options) return read<T>(path);
  return rawRequest<T>(path, options);
}

/** A GET that may share a running listing-screen read; `signal` aborts only this caller's wait. */
function read<T>(path: string, options?: ReadOptions): Promise<T> {
  return listingScreenRead<T>(path, (readPath, signal) => rawRequest(readPath, signal ? { signal } : undefined), options?.signal);
}

/** What a proxy answers while the server behind it is down or restarting, such as during a deploy. */
const UNAVAILABLE_STATUSES = new Set([502, 503, 504]);

async function rawRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const isRead = (options?.method ?? "GET").toUpperCase() === "GET";
  let res: Response;
  try {
    res = await studioFetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...options?.headers },
      ...options,
    });
  } catch (error) {
    // fetch rejects with a TypeError when the connection fails or drops.
    if (isRead && error instanceof TypeError) throw new ApiRequestError(error.message, 0, true);
    throw error;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiRequestError(
      errorMessage(body, `Request failed: ${res.status}`),
      res.status,
      isRead && UNAVAILABLE_STATUSES.has(res.status),
      retryAfterMs(res.headers.get("Retry-After")),
      res.headers.get("X-Anteroom-Reason"),
    );
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
    if (controller.signal.aborted) {
      const failure = new Error(timeoutMessage);
      // Route shape only: never send listing IDs, request bodies or query values.
      const action = path.endsWith("/reset") ? "reset"
        : path.endsWith("/price-drop") ? "price confirmation" : "batch price confirmation";
      reportError("mutation", new Error(`${action} timed out after ${timeoutMs}ms. The server may still complete the save.`));
      throw failure;
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export interface PublishedVersion {
  tag: string;
  version: string;
  short_sha: string;
  title: string;
  published_at: string | null;
  url: string | null;
  installed: boolean;
}

export interface PublishedVersions {
  packaged: boolean;
  versions: PublishedVersion[];
  error: string | null;
}

export type VersionPlan = {
  tag: string;
  version: string | null;
  title: string | null;
} & (
  | { action: "keep" }
  | { action: "blocked"; reason: string }
  | { action: "restore"; snapshot: { name: string; taken_at: string; reason: string; size_bytes: number } }
);

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
  vendooDraft: {
    check: (id: string) => request<VendooDraft>(`/conversations/${id}/vendoo-draft/check`, { method: "POST" }),
    status: (id: string) => request<VendooDraft>(`/conversations/${id}/vendoo-draft`),
    send: (id: string, revisionId: string) => request<VendooDraft>(`/conversations/${id}/vendoo-draft`, { method: "POST", body: JSON.stringify({ revision_id: revisionId }) }),
  },
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
    choiceList: (name: string, version: string) =>
      request<ChoiceList>(`/catalog/choice-lists/${encodeURIComponent(name)}?v=${encodeURIComponent(version)}`),
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
    importListing: (listingId: string, linkConversationId?: string) => request<MarketplaceImportResult>("/etsy/import", {
      method: "POST", body: JSON.stringify({ listing_id: listingId, ...(linkConversationId ? { link_conversation_id: linkConversationId } : {}) }),
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
    /** Sales figures, weekly sales and sales by marketplace for the Sales records filters. */
    overview: (filters: { start: string | null; end: string; marketplace?: string }) =>
      request<SalesOverview>(`/analytics/overview?${salesOverviewParams(filters)}`),
  },
  measurements: {
    /** Suggest measurements from an item's yardstick photos. Nothing is saved. */
    read: async (files: File[], garment: string): Promise<MeasurementReadResult> => {
      const form = new FormData();
      files.forEach((file) => form.append("files", file));
      form.append("garment", garment);
      // A read takes minutes, so a deploy or restart can cut it off. That is tried
      // once more; an error the server explains (a model that failed) is not.
      for (let attempt = 1; ; attempt++) {
        let response: Response;
        try {
          response = await studioFetch(`${BASE}/measurements/read`, { method: "POST", body: form });
        } catch (error) {
          if (error instanceof TypeError && attempt === 1) continue;
          throw error;
        }
        const data = await response.json().catch(() => null);
        if (response.ok) return data as MeasurementReadResult;
        const explained = typeof data?.detail === "string" && data.detail.trim();
        if (!explained && response.status >= 500) {
          if (attempt === 1) continue;
          throw new Error("The server restarted while reading. Press Read measurements again.");
        }
        throw new Error(errorMessage(data, `Reading failed: ${response.status}`));
      }
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
    threads: (options?: ReadOptions) => read<AssistantThread[]>("/assistant/threads", options),
    createThread: () => request<AssistantThread>("/assistant/threads", { method: "POST" }),
    deleteThread: (id: string) => request<{ ok: boolean }>(`/assistant/threads/${id}`, { method: "DELETE" }),
    messages: (threadId?: string, options?: ReadOptions) =>
      read<AssistantMessage[]>(`/assistant/messages${threadId ? `?thread_id=${encodeURIComponent(threadId)}` : ""}`, options),
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
    /** Answer the thread's last question again, when nothing answered it (an interrupted or failed answer). */
    retry: (handlers: AssistantStreamHandlers, signal: AbortSignal | undefined, threadId: string) =>
      followAssistant(
        `/assistant/messages/retry?thread_id=${encodeURIComponent(threadId)}`,
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
    // A stalled read ends in an error with Try again, not an endless skeleton.
    get: () => requestWithTimeout<SourcingState>("/sourcing", {}, "Sourcing took too long to answer."),
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
    importListing: (listingId: string, linkConversationId?: string) => request<MarketplaceImportResult>("/ebay/import", {
      method: "POST", body: JSON.stringify({ listing_id: listingId, ...(linkConversationId ? { link_conversation_id: linkConversationId } : {}) }),
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
    finish: (marketplace: DirectMarketplace, state: string, claim: string) =>
      request<{ marketplace: DirectMarketplace; label: string | null }>(`/marketplace-accounts/${marketplace}/finish`, {
        method: "POST", body: JSON.stringify({ state, claim }),
      }),
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
    checkSales: (convId: string) =>
      request<SalesCheck>(`/conversations/${convId}/marketplace-listings/check-sales`, { method: "POST" }),
  },
  nativeMarketplaces: {
    refreshForm: (convId: string, marketplace: string, revisionId: string) =>
      request<{ revision_id: string }>(`/conversations/${convId}/native-marketplaces/${marketplace}/form/refresh`, {
        method: "POST", body: JSON.stringify({ revision_id: revisionId }),
      }),
    form: (convId: string, marketplace: string, revisionId?: string | null) =>
      request<MarketplaceForm>(`/conversations/${convId}/native-marketplaces/${marketplace}/form${revisionId ? `?revision_id=${encodeURIComponent(revisionId)}` : ""}`),
    status: (convId: string) => request<NativeDrafts>(`/conversations/${convId}/native-marketplaces`),
    preflight: (convId: string) =>
      request<NativePreflight>(`/conversations/${convId}/native-marketplaces/preflight`),
    /** The fields each listing still needs per marketplace; the inventory asks for the rows on screen. */
    readiness: (convIds: readonly string[]) =>
      request<{ listings: Record<string, ListingReadiness> }>(
        `/listing-readiness?${convIds.map((id) => `ids=${encodeURIComponent(id)}`).join("&")}`),
    saveDraft: (convId: string, marketplace: NativeMarketplace, revisionId: string) =>
      request<NativeDraft>(`/conversations/${convId}/native-marketplaces/${marketplace}/draft`, {
        method: "POST", body: JSON.stringify({ revision_id: revisionId }),
      }),
    list: (convId: string, marketplace: NativeMarketplace, revisionId: string) =>
      request<NativeDraft>(`/conversations/${convId}/native-marketplaces/${marketplace}/list`, {
        method: "POST", body: JSON.stringify({ revision_id: revisionId, confirm: true }),
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
  /** Hosted only: deletes the signed-in seller's account. Live marketplace listings are left alone. */
  account: {
    delete: () => request<{ deleted: boolean }>("/account", { method: "DELETE", body: JSON.stringify({ confirm: true }) }),
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
    remove: (saleId: string, expectedRevision: number) => request<{ deleted: string }>(
      `/sales-ledger/${encodeURIComponent(saleId)}?${new URLSearchParams({ expected_revision: String(expectedRevision) })}`, { method: "DELETE" }),
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
  health: () => request<{ status: string; version: string }>("/health"),

  status: (options?: ReadOptions) =>
    read<{
      version: string;
      provider_configured: boolean;
      extension_connected: boolean;
      active_job_id: string | null;
      packaged?: boolean;
      chrome_available?: boolean;
      conversations?: number;
      setup_guide_dismissed?: boolean;
      data_dir?: string;
    }>("/status", options),

  conversations: {
    copyBatchPreview: (ids: string[]) => request<{ rows: BatchCopyPreviewRow[] }>("/listings/copies/preview", { method: "POST", body: JSON.stringify({ conversation_ids: ids }) }),
    copyResult: (id: string, requestId: string) => request<Conversation>(`/conversations/${id}/copy-result/${requestId}`),
    copyPreview: (id: string) => request<CopyPreview>(`/conversations/${id}/copy-preview`),
    copy: (id: string, body: CopyListingRequest) => request<Conversation>(`/conversations/${id}/copy`, { method: "POST", body: JSON.stringify(body) }),
    list: (options?: ReadOptions) => read<ConversationSummary[]>("/conversations", options),
    get: (id: string, options?: ReadOptions) => read<Conversation>(`/conversations/${id}`, options),
    /** Item Details as Regenerate will start from them; nothing is saved. */
    regenerateDetails: (id: string) => read<{ notes: string | null }>(`/conversations/${id}/regenerate-details`),
    create: (body?: ConversationInput) =>
      request<Conversation>("/conversations", { method: "POST", body: JSON.stringify(body || {}) }),
    update: (id: string, body: ConversationInput) =>
      request<Conversation>(`/conversations/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    /** Deletes a new listing only while it is still blank; the server decides. `keepalive` outlives a closing tab. */
    discardBlank: (id: string, options?: { keepalive?: boolean }) =>
      request<{ discarded: boolean }>(`/conversations/${id}/discard-blank`, { method: "POST", keepalive: options?.keepalive }),
    settle: (id: string) =>
      request<Conversation>(`/conversations/${id}/settle`, { method: "POST" }),
    unsettle: (id: string) =>
      request<Conversation>(`/conversations/${id}/unsettle`, { method: "POST" }),
    stop: (id: string) =>
      request<{ ok: boolean }>(`/conversations/${id}/stop`, { method: "POST" }),
    activity: (id: string, options?: ReadOptions) => read<ConversationActivity>(`/conversations/${id}/activity`, options),
    messages: (id: string, options?: ReadOptions) => read<Message[]>(`/conversations/${id}/messages`, options),
    /** The item's photos, or with `marketplace` the photos added on that marketplace's form only. */
    photos: (id: string, marketplace?: string, options?: ReadOptions) =>
      read<ListingPhoto[]>(`/conversations/${id}/photos${marketplace ? `?marketplace=${encodeURIComponent(marketplace)}` : ""}`, options),
    deletePhoto: (convId: string, photoId: string) =>
      request<OkResponse>(`/conversations/${convId}/photos/${photoId}`, { method: "DELETE" }),
    /** Rotate the stored image clockwise by `degrees` (90, -90 or 180). */
    rotatePhoto: (convId: string, photoId: string, degrees: 90 | -90 | 180) =>
      request<ListingPhoto>(`/conversations/${convId}/photos/${photoId}/rotate`, {
        method: "POST",
        body: JSON.stringify({ degrees }),
      }),
    /** Replace a photo's edits; the server renders them from the kept original. */
    editPhoto: (convId: string, photoId: string, edits: PhotoEdits) =>
      request<ListingPhoto>(`/conversations/${convId}/photos/${photoId}/edits`, {
        method: "PUT",
        body: JSON.stringify(edits),
      }),
    resetPhoto: (convId: string, photoId: string) =>
      request<ListingPhoto>(`/conversations/${convId}/photos/${photoId}/edits`, { method: "DELETE" }),
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
    uploadPhotos: (convId: string, files: File[], marketplace?: string): Promise<PhotoUploadResult> => trackPhotoUpload(convId, async () => {
      if (hasHostedApiSession()) return (async () => {
        const ids: string[] = []; const errors: string[] = [];
        for (const file of files) {
          if (file.size > 10 * 1024 * 1024) { errors.push(`${file.name}: choose a photo smaller than 10 MB.`); continue; }
          const query = marketplace ? `?marketplace=${encodeURIComponent(marketplace)}` : "";
          const response = await studioFetch(`${BASE}/drafts/${encodeURIComponent(convId)}/photos${query}`, { method: "POST", body: file });
          if (!response.ok) { errors.push(`${file.name}: could not upload this photo.`); continue; }
          ids.push((await response.json()).id);
        }
        const photos = await request<ListingPhoto[]>(`/conversations/${encodeURIComponent(convId)}/photos${marketplace ? `?marketplace=${encodeURIComponent(marketplace)}` : ""}`);
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
    }),
  },

  similarItems: {
    list: () => request<SimilarItems>("/similar-items"),
    dismiss: (firstId: string, secondId: string) =>
      request<{ ok: boolean }>("/similar-items/dismiss", { method: "POST", body: JSON.stringify({ first_id: firstId, second_id: secondId }) }),
    merge: (keepId: string, mergeId: string) =>
      request<SimilarMerge>("/similar-items/merge", { method: "POST", body: JSON.stringify({ keep_id: keepId, merge_id: mergeId }) }),
  },

  listings: {
    get: (convId: string, options?: ReadOptions) => read<ListingDocument>(`/conversations/${convId}/listing`, options),
    /** The marketplace forms for this listing's category (the eBay and Etsy editors read these). */
    fields: (convId: string, options?: ReadOptions) =>
      read<{ ok: boolean; forms: MarketplaceForm[] }>(`/conversations/${convId}/vendoo-api/fields`, options),
    update: (convId: string, listing: ListingData, expectedRevisionId: string) =>
      request<ListingSaveResult>(`/conversations/${convId}/listing`, {
        method: "PUT",
        body: JSON.stringify({ listing, expected_revision_id: expectedRevisionId } satisfies ListingSave),
      }),
    validate: (convId: string) =>
      request<ListingValidation>(`/conversations/${convId}/listing/validate`, { method: "POST" }),
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
    /** Save confirmed prices as Studio revisions. */
    applyMarkdowns: (drops: MarkdownDropRequest[]) =>
      requestWithTimeout<MarkdownApplyResult>("/price-drop/batch", {
        method: "POST",
        body: JSON.stringify({ drops }),
      }, "Saving the prices took too long. Check the listings' prices before applying again; you do not need to restart Anteroom."),
  },

  jobs: {
    queue: (options?: ReadOptions) => read<{
      work: { conversation_id: string; title: string; detail: string }[];
      jobs: Job[];
    }>("/jobs/queue", options),
    list: (conversationId?: string, options?: ReadOptions) => {
      const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : "";
      return read<Job[]>(`/jobs${query}`, options);
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
        listing_mode: "direct" | "vendoo";
        listing_names: ListingNames;
        model_favorites: ModelFavorite[];
        stale_listing_days: number;
        label_colors: Record<string, LabelColor>;
      }>("/settings/ui"),
    setUi: (body: {
      recent_vendoo_labels?: string[];
      settled_shelf_expanded?: boolean;
      theme?: "dark" | "light" | "system";
      listing_mode?: "direct" | "vendoo";
      listing_names?: ListingNames;
      remember_labels?: string | string[];
      restore_labels?: string[];
      forget_label?: string;
      model_favorites?: ModelFavorite[];
      stale_listing_days?: number;
      label_colors?: Record<string, LabelColor>;
    }) =>
      request<{
        ok: boolean;
        recent_vendoo_labels: string[];
        settled_shelf_expanded: boolean;
        hidden_vendoo_labels: string[];
        theme: "dark" | "light" | "system";
        listing_mode: "direct" | "vendoo";
        listing_names: ListingNames;
        model_favorites: ModelFavorite[];
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
    errorLogs: () => request<{ entries: StudioErrorLogEntry[] }>("/settings/error-logs"),
    errorEmailDelivery: () => request<{
      visible: boolean;
      state?: "disabled" | "needs_budget" | "ready" | "quota_paused" | "delivery_failed" | "budget_exhausted";
      daily_limit?: number;
      used?: number;
      pending?: number;
      resume_at?: number | null;
      last_error?: string | null;
      last_success_at?: number | null;
      dropped?: number;
    }>("/error-reports/delivery"),
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
        runs_marketplace_actions?: boolean;
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
    versions: () => request<PublishedVersions>("/updates/versions"),
    versionPlan: (tag: string) => request<VersionPlan>(`/updates/versions/${encodeURIComponent(tag)}/plan`),
    installVersion: (tag: string, snapshot?: string | null) =>
      request<{ ok: boolean; updated: boolean; version?: string; reloading?: boolean; database_restored?: boolean }>(
        "/updates/versions/install",
        { method: "POST", body: JSON.stringify({ tag, snapshot: snapshot ?? null }) },
      ),
  },

  changelog: () =>
    request<{
      version: string;
      entries: { version: string; date: string | null; body: string }[];
    }>("/changelog"),
};
