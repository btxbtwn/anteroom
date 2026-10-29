import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangleIcon, CheckCircle2Icon, CircleIcon, EllipsisIcon, ExternalLinkIcon, MinusCircleIcon, ReceiptIcon, SendIcon, Trash2Icon, XCircleIcon } from "lucide-react";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { DelistMarketplace, FacebookCheck, MarketplaceListing, MarketplaceListings, MarketplaceMark, SaleMarketplace, SellerSale } from "../api/marketplaceListings";
import type { NativeDraft, NativeDrafts, NativeMarketplace, PreflightGap } from "../api/nativeMarketplaces";
import { EXTENSION_SESSION_MARKETS, type ExtensionSessionConnect, type ExtensionSessionMarket } from "../api/marketplaceSessions";
import { cn } from "../lib/utils";
import { confirmDialog } from "../ui/confirmDialog";
import { MarketplaceLogo } from "./MarketplaceLogo";
import { MarkSoldForm } from "./MarkSoldForm";
import { marketplaceName } from "./marketplaceNames";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Menu, MenuItem, MenuPopup, MenuSeparator, MenuTrigger } from "./ui/menu";
import { Spinner } from "./ui/spinner";
import { MarketplaceMessagesContext, uniqueMarketplaceMessages } from "./marketplaceMessages";

type Marketplace = DelistMarketplace;
const ORDER: Marketplace[] = ["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild", "vinted", "facebook"];
const NATIVE = new Set<Marketplace>(["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild", "vinted"]);
const REMOTE_DRAFT = new Set<Marketplace>(["ebay", "mercari", "depop", "etsy", "poshmark"]);
// What Delist does on each marketplace: eBay has no delete; Sellwild offers only its own delist.
// Vinted is deleted through the signed-in Chrome session, the same as Depop, Poshmark and Mercari.
// Facebook listings are only linked and watched; the seller deletes them on Facebook.
const REMOVED: Record<Marketplace, string> = {
  ebay: "Ended", etsy: "Deleted", poshmark: "Deleted", mercari: "Deleted", depop: "Deleted", sellwild: "Delisted", vinted: "Deleted",
  facebook: "Deleted",
};
const FACEBOOK_STATE: Record<NonNullable<FacebookCheck["state"]>, string> = {
  live: "Listed on Facebook.", pending: "Marked pending on Facebook.", sold: "Marked sold on Facebook.",
  hidden: "Hidden on Facebook.", draft: "Only a draft on Facebook.", not_live: "Not live on Facebook.",
};

type Tone = "live" | "neutral" | "busy" | "attention" | "failed" | "idle";
type Status = { tone: Tone; label: string; href?: string; hrefLabel?: string; detail?: string | null; said?: string | null };

const BUSY = new Set(["saving", "publishing", "updating"]);
type ActionKind = "check" | "list" | "save_draft" | "update" | "cancel" | "reset" | "reconnect";
export const ETSY_NOT_ELIGIBLE = "Etsy only allows vintage (20+ years), handmade or craft supplies. This item doesn't qualify.";

/** A record whose job is running now. A busy state with no job running is unconfirmed until checked. */
export function isRunning(draft: NativeDraft | undefined): boolean {
  return Boolean(draft?.running) || (BUSY.has(draft?.state ?? "") && draft?.running === undefined);
}

/** What a reconnect found, so a success is not hidden behind the last refusal. */
export function reconnectText(name: string, session: ExtensionSessionConnect | undefined): string | null {
  if (!session) return null;
  if (session.signed_in) return `${name} is connected${session.label ? ` as ${session.label}` : ""}. Press List… again to list this item.`;
  return `${name} still needs a sign-in. Finish signing in in the Chrome tab, then choose Reconnect ${name} again.`;
}

/** The words chat gets for a refused List: which marketplace, both explanations, and the fields to fix. */
export function askChatPrompt(marketplace: Marketplace, draft: NativeDraft): string {
  const name = marketplaceName(marketplace);
  const lines = [`Listing this item on ${name} failed. Anteroom said: ${draft.error || "the listing was refused."}`];
  if (draft.marketplace_message) lines.push(`${name} said: ${draft.marketplace_message}`);
  if (/could not find draft/i.test(`${draft.error || ""} ${draft.marketplace_message || ""}`)) {
    lines.push(`The saved ${name} draft was deleted. Leave the listing fields as they are. I will press List again so Anteroom starts a new draft.`);
    return lines.join("\n");
  }
  if (draft.error_fields?.length) lines.push(`Fields: ${draft.error_fields.map((item) => `${item.field} (${item.message})`).join("; ")}`);
  lines.push(`Fill or fix those fields in this listing so ${name} accepts it. Do not list it; I will press List again.`);
  return lines.join("\n");
}

function fieldLabel(field: string): string {
  if (field === "settings.ebay.merchant_location_key") return "Inventory location";
  const last = field.split(".").pop() || field;
  return last.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, (c) => c.toUpperCase());
}

function secondsUntil(iso: string | null | undefined, now: number): number {
  const at = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(at) ? Math.max(0, Math.ceil((at - now) / 1000)) : 0;
}

const TONE_ICON: Record<Tone, ReactNode> = {
  live: <CheckCircle2Icon aria-hidden className="size-3.5 text-success" />,
  neutral: <MinusCircleIcon aria-hidden className="size-3.5 text-muted-foreground" />,
  busy: <Spinner size="sm" tone="muted" />,
  attention: <AlertTriangleIcon aria-hidden className="size-3.5 text-warning" />,
  failed: <XCircleIcon aria-hidden className="size-3.5 text-destructive" />,
  idle: <CircleIcon aria-hidden className="size-3.5 text-muted-foreground/60" />,
};

export function deletable(entry: MarketplaceListing | undefined): boolean {
  return Boolean(entry?.listing_id && entry.can_delist !== false && !entry.sold_here && entry.state !== "deleted" && entry.state !== "deleting");
}

/** What a Facebook check found, in the seller's words. */
export function facebookCheckText(check: FacebookCheck): string {
  if (!check.ok) return check.error || "Chrome could not read the Facebook listing.";
  const parts = [check.state ? FACEBOOK_STATE[check.state] : "Facebook did not say."];
  if (check.owner === "other") parts.push("It is on a different Facebook account than the one signed in to Chrome.");
  if (check.sale_recorded) parts.push("Anteroom recorded the sale.");
  return parts.join(" ");
}

/** One status per marketplace. A live listing wins. Anything else is not listed; Anteroom holds the listing. */
export function rowStatus(marketplace: Marketplace, live: MarketplaceListing | undefined, draft: NativeDraft | undefined,
  _revisionId: string | null, etsyEligible: boolean): Status {
  if (live?.sold_here) return { tone: "neutral", label: "Sold here", href: live.url ?? undefined, hrefLabel: "View" };
  const said = draft?.marketplace_message ? `${marketplaceName(marketplace)} said: ${draft.marketplace_message}` : null;
  if (draft && isRunning(draft)) {
    return { tone: "busy", label: draft.state === "updating" ? "Updating…" : draft.action === "save_draft" ? "Saving draft…" : "Listing…", detail: draft.progress ? `${draft.progress.label}…` : null };
  }
  if (draft && (draft.state === "unknown" || BUSY.has(draft.state))) {
    return { tone: "attention", label: "Unconfirmed", detail: draft.error || "Check the result before trying again.", said };
  }
  if (live?.state === "deleting") return { tone: "busy", label: "Delisting…" };
  if (live?.state === "deleted") return { tone: "neutral", label: REMOVED[marketplace] };
  if (live?.state === "failed") {
    return { tone: "failed", label: "Not delisted", href: live.url ?? undefined, hrefLabel: "View", detail: live.error,
      said: live.marketplace_message ? `${marketplaceName(marketplace)} said: ${live.marketplace_message}` : null };
  }
  // Anteroom records the revision it last sent, so it knows when the listing has
  // moved on. Without that record (listed elsewhere, or marked by hand) it can't tell.
  const behind = draft?.state === "published" && Boolean(draft.revision_id) && draft.revision_id !== _revisionId
    ? `Your latest edits aren't on ${marketplaceName(marketplace)} yet.` : null;
  if (live?.listing_id) {
    return { tone: "live", label: "Listed", href: live.url ?? undefined, hrefLabel: "View listing", detail: draft?.state === "published" ? draft.error || behind : null, said };
  }
  if (draft && draft.state !== "draft") {
    if (draft.state === "published") return { tone: "live", label: "Listed", href: draft.listing_url, hrefLabel: "View listing", detail: draft.error || behind };
    if (draft.state === "pending_review") return { tone: "attention", label: "In review", href: draft.listing_url, hrefLabel: "View" };
    if (draft.error) return { tone: "failed", label: "Not listed", detail: draft.error, said };
  }
  if (draft?.state === "draft" && draft.error) return { tone: "failed", label: "Not listed", detail: draft.error, said };
  if (draft?.state === "draft" && draft.draft_url) return { tone: "neutral", label: "Draft", href: draft.draft_url, hrefLabel: "View draft",
    detail: draft.revision_id !== _revisionId ? "Changes in Anteroom have not been saved to this draft." : null };
  // eBay keeps a saved draft as an unpublished offer, which has no page to open.
  if (draft?.state === "draft" && marketplace === "ebay") return { tone: "neutral", label: "Saved on eBay",
    detail: draft.revision_id !== _revisionId ? "Changes in Anteroom have not been saved to eBay." : "Not listed. Buyers cannot see it until you List." };
  if (marketplace === "etsy" && !etsyEligible) return { tone: "attention", label: "Not eligible", detail: ETSY_NOT_ELIGIBLE };
  return { tone: "idle", label: "Not listed" };
}


/**
 * The answers this pane still says: every gap the open form does not read under a
 * row of its own. One the form shows is read there, where the seller fixes it.
 */
export function saidBesideList(gaps: readonly PreflightGap[], shownOnForm: readonly string[]): PreflightGap[] {
  const onForm = new Set(shownOnForm);
  return gaps.filter((gap) => !onForm.has(gap.field));
}

function railLabel(id: string): string {
  if (id === "general") return "General";
  return marketplaceName(id);
}

const MANAGED = new Set<string>(ORDER);

type MarketplaceProps = {
  convId: string; revisionId: string | null; title: string; disabled: boolean; etsyEligible: boolean;
  /** Send a prompt to the listing's chat; absent while chat is busy. */
  onAskChat?: (text: string) => void;
  /**
   * Open the editor at a listing field, e.g. "price" or "ebay_specifics.category_specifics.Color".
   * A marketplace's own answer names the field its way, so the marketplace
   * comes along: that form holds the field the answer is about.
   */
  onEditField?: (field: string, marketplace?: string) => void;
};

/** Where the open form tells this pane which fields it draws a row for. */
const FieldsShownContext = createContext<((marketplace: string, fields: ReadonlySet<string>) => void) | undefined>(undefined);

/**
 * Tell the marketplace pane which fields this form draws a row for, so an answer
 * about one of them is read under that row rather than a second time beside List.
 * Fields are compared by name, so the editor reports them unchanged across saves.
 */
export function useReportFieldsShown(marketplace: string, fields: readonly string[]): void {
  const report = useContext(FieldsShownContext);
  // Sorted and joined, so the same answers on the next render report once.
  const signature = [...fields].sort().join("\n");
  useEffect(() => { report?.(marketplace, new Set(signature ? signature.split("\n") : [])); }, [report, marketplace, signature]);
}

export function MarketplacesLayout({
  convId, revisionId, title, disabled, etsyEligible, onAskChat, onEditField,
  ids, selected, onSelect, railActions, railStatus, children,
}: MarketplaceProps & {
  ids: readonly string[];
  /** Listing-wide actions pinned above the rail's own, e.g. Regenerate. */
  railActions?: ReactNode;
  /** The listing's sync state and overflow menu, the rail's last line. */
  railStatus?: ReactNode;
  selected: string;
  onSelect: (id: string) => void;
  children?: ReactNode;
}) {
  const queryClient = useQueryClient();
  // Which fields the open form draws a row for, so an answer about one of them is
  // read there and not twice.
  const [fieldsShown, setFieldsShown] = useState<Partial<Record<string, ReadonlySet<string>>>>({});
  const reportFields = useCallback((marketplace: string, fields: ReadonlySet<string>) => {
    setFieldsShown((current) => {
      const before = current[marketplace];
      // The editor reports on every save; only a different set of rows redraws the pane.
      if (before && before.size === fields.size && [...fields].every((field) => before.has(field))) return current;
      return { ...current, [marketplace]: fields };
    });
  }, []);
  const nativeKey = ["native-marketplaces", convId];
  const liveKey = ["marketplace-listings", convId];
  // List runs in the background: poll while any marketplace action is running.
  const native = useQuery({ queryKey: nativeKey, queryFn: () => api.nativeMarketplaces.status(convId),
    refetchInterval: (query) => Object.values(query.state.data ?? {}).some((draft) => isRunning(draft)) ? 1500 : false });
  const live = useQuery({ queryKey: liveKey, queryFn: () => api.marketplaceListings.get(convId) });
  const drafts: NativeDrafts = native.data ?? {};
  const runningKey = Object.entries(drafts).filter(([, draft]) => isRunning(draft)).map(([name]) => name).join(",");
  const wasRunning = useRef(runningKey);
  useEffect(() => {
    // An action just ended: what is live, and the job list, changed with it.
    if (wasRunning.current && wasRunning.current !== runningKey) {
      void queryClient.invalidateQueries({ queryKey: liveKey });
      void queryClient.invalidateQueries({ queryKey: ["jobs", convId] });
    }
    wasRunning.current = runningKey;
  });
  const [now, setNow] = useState(() => Date.now());
  const waiting = Object.values(drafts).some((draft) => secondsUntil(draft?.retry_after, now) > 0);
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [waiting]);
  const listings: MarketplaceListings = live.data ?? {};
  const [draftLinks, setDraftLinks] = useState<Partial<Record<NativeMarketplace, string>>>({});
  // What the last reconnect found, so the stale refusal does not read as the answer.
  const [reconnected, setReconnected] = useState<Partial<Record<Marketplace, ExtensionSessionConnect>>>({});
  const [linking, setLinking] = useState<{ marketplace: Marketplace; url: string } | null>(null);
  // Mark as sold opens a sale form; the number remounts it with a fresh request id.
  const [selling, setSelling] = useState<{ marketplace: SaleMarketplace; key: number } | null>(null);

  const etsyCreating = !listings.etsy?.listing_id && !drafts.etsy?.draft_url;
  const etsyDefaults = useQuery({ queryKey: ["etsy-listing-defaults"], queryFn: () => api.etsy.listingDefaults(),
    enabled: etsyCreating, staleTime: 60_000, retry: false });
  const etsyReady = Boolean(etsyDefaults.data?.shipping_profile_id && etsyDefaults.data?.readiness_state_id && etsyDefaults.data?.return_policy_id);
  const preflight = useQuery({
    queryKey: ["native-preflight", convId, revisionId],
    queryFn: () => api.nativeMarketplaces.preflight(convId),
    enabled: Boolean(revisionId),
    staleTime: 15_000,
    // Every save is a new revision. Keep the last answer on screen until the new one
    // arrives, so the pane above the form does not collapse and shift the form.
    placeholderData: (previous) => previous,
  });

  // Each marketplace tracks its own request, so starting one never locks the others.
  const [starting, setStarting] = useState<Partial<Record<NativeMarketplace, ActionKind>>>({});
  const [actionErrors, setActionErrors] = useState<Partial<Record<NativeMarketplace, string>>>({});
  const action = useMutation({
    mutationFn: async ({ marketplace, kind, draftUrl }: { marketplace: NativeMarketplace; kind: ActionKind; draftUrl?: string }) => {
      if (kind === "check") return api.nativeMarketplaces.check(convId, marketplace, draftUrl);
      if (kind === "cancel") return api.nativeMarketplaces.cancel(convId, marketplace);
      if (kind === "reset") return api.nativeMarketplaces.reset(convId, marketplace);
      if (kind === "reconnect") {
        if (marketplace === "ebay" || marketplace === "etsy") return await api.directConnections.connect(marketplace);
        if ((EXTENSION_SESSION_MARKETS as readonly string[]).includes(marketplace)) {
          return await api.marketplaceSessions.connect(marketplace as ExtensionSessionMarket);
        }
        return undefined;
      }
      if (!revisionId) throw new Error("Generate a listing first.");
      if (kind === "save_draft") return api.nativeMarketplaces.saveDraft(convId, marketplace, revisionId);
      if (kind === "update") return api.nativeMarketplaces.update(convId, marketplace, revisionId);
      return api.nativeMarketplaces.list(convId, marketplace, revisionId);
    },
    retry: false,
    onMutate: ({ marketplace, kind }) => {
      setStarting((current) => ({ ...current, [marketplace]: kind }));
      setActionErrors((current) => ({ ...current, [marketplace]: undefined }));
      setReconnected((current) => ({ ...current, [marketplace]: undefined }));
    },
    onError: (error, { marketplace }) => setActionErrors((current) => ({ ...current, [marketplace]: error.message })),
    onSuccess: (data, { marketplace, kind }) => {
      if (kind !== "reconnect") return;
      const session = data as ExtensionSessionConnect | undefined;
      if (typeof session?.signed_in !== "boolean") return;
      void queryClient.invalidateQueries({ queryKey: ["marketplace-sessions"] });
      setReconnected((current) => ({ ...current, [marketplace]: session }));
    },
    onSettled: (_data, _error, { marketplace }) => {
      setStarting((current) => ({ ...current, [marketplace]: undefined }));
      void queryClient.invalidateQueries({ queryKey: nativeKey });
      void queryClient.invalidateQueries({ queryKey: ["native-editor-form", convId] });
      void queryClient.invalidateQueries({ queryKey: liveKey });
      void queryClient.invalidateQueries({ queryKey: ["jobs", convId] });
    },
  });
  const delist = useMutation({
    mutationFn: (marketplaces: Marketplace[]) => api.marketplaceListings.delist(convId, marketplaces),
    retry: false,
    onSuccess: (data) => queryClient.setQueryData(liveKey, data),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: liveKey });
      // The sidebar row drops the delisted marketplaces and may read Draft.
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
  const mark = useMutation({
    mutationFn: ({ marketplace, state, url }: { marketplace: Marketplace; state: MarketplaceMark; url?: string }) =>
      api.marketplaceListings.mark(convId, marketplace, state, url),
    retry: false,
    onSuccess: (data) => { queryClient.setQueryData(liveKey, data); setLinking(null); },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: liveKey });
      void queryClient.invalidateQueries({ queryKey: nativeKey });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const checkFacebook = useMutation({
    mutationFn: () => api.marketplaceListings.checkFacebook(convId),
    retry: false,
    onSuccess: (data) => {
      queryClient.setQueryData(liveKey, data.listings);
      if (data.sale_recorded) {
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
        void queryClient.invalidateQueries({ queryKey: ["sales-ledger"] });
      }
    },
  });

  const recordSale = useMutation({
    mutationFn: (sale: SellerSale) => api.marketplaceListings.recordSale(convId, sale),
    retry: false,
    onSuccess: (data) => {
      queryClient.setQueryData(liveKey, data.listings);
      setSelling(null);
      // Offered, never done here: the seller confirms the delist in its own dialog.
      if (data.delist_offer.length) void runDelist(data.delist_offer, "Sale recorded. Delist it from the other marketplaces too?");
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: liveKey });
      void queryClient.invalidateQueries({ queryKey: nativeKey });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      void queryClient.invalidateQueries({ queryKey: ["sales-ledger"] });
      void queryClient.invalidateQueries({ queryKey: ["analytics"] });
    },
  });
  function startSale(marketplace: SaleMarketplace) {
    recordSale.reset();
    setSelling((current) => ({ marketplace, key: (current?.key ?? 0) + 1 }));
  }

  async function updateNow(marketplace: NativeMarketplace) {
    const name = marketplaceName(marketplace);
    const extra = marketplace === "sellwild" ? " Sellwild may hold the edit for review." : "";
    if (await confirmDialog(`Update the live ${name} listing for “${title}” with what's saved in Anteroom?\nThis changes the listing that is already up.${extra}`,
      { confirmLabel: `Update on ${name}` })) action.mutate({ marketplace, kind: "update" });
  }
  async function listNow(marketplace: NativeMarketplace) {
    const name = marketplaceName(marketplace);
    const extra = marketplace === "sellwild" ? " Sellwild may hold it for review."
      : marketplace === "ebay" ? " This uses your eBay shipping, payment, and return policies, set in Settings → Connections → eBay when you have more than one."
      : marketplace === "etsy" && !etsyEligible ? " Etsy may still reject it if the item is not vintage, handmade, or a supply."
      : "";
    if (await confirmDialog(`List “${title}” on ${name}?\nThis creates a live ${name} listing from the listing saved in Anteroom.${extra}`,
      { confirmLabel: `List on ${name}` })) action.mutate({ marketplace, kind: "list" });
  }
  /** Starts List on each marketplace named, after one confirmation that names them all. */
  async function listEverywhere(marketplaces: NativeMarketplace[], skipped: Marketplace[]) {
    const lines = marketplaces.map((marketplace) => `• ${marketplaceName(marketplace)}`).join("\n");
    const left = skipped.length ? `\nNot on ${skipped.map(marketplaceName).join(", ")}: list those from their own page once they're ready.` : "";
    if (!(await confirmDialog(`List “${title}” on ${marketplaces.length} marketplaces?\n${lines}\nEach one gets a live listing from the listing saved in Anteroom.${left}`,
      { confirmLabel: `List on ${marketplaces.length}` }))) return;
    for (const marketplace of marketplaces) action.mutate({ marketplace, kind: "list" });
  }
  async function runDelist(marketplaces: Marketplace[], lead?: string) {
    const names = marketplaces.map(marketplaceName);
    const lines = marketplaces.map((marketplace) => `• ${marketplaceName(marketplace)}: ${REMOVED[marketplace].toLowerCase()}`).join("\n");
    const question = `Delist “${title}” from ${names.length === 1 ? names[0] : `${names.length} marketplaces`}?`;
    const approved = await confirmDialog(`${lead ? `${lead}\n` : ""}${question}\n${lines}\nA deleted listing can't be restored; listing again creates a new one.`,
      { variant: "destructive", confirmLabel: names.length === 1 ? `Delist from ${names[0]}` : `Delist from ${names.length}` });
    if (approved) delist.mutate(marketplaces);
  }

  async function resetNow(marketplace: NativeMarketplace) {
    const name = marketplaceName(marketplace);
    if (await confirmDialog(`Did you check ${name} yourself?\nAnteroom will treat “${title}” as not listed on ${name}, so you can List again. Nothing on ${name} changes. If the listing is there, use Mark as listed instead.`,
      { confirmLabel: "I checked it myself" })) action.mutate({ marketplace, kind: "reset" });
  }

  const busy = disabled || delist.isPending || mark.isPending || recordSale.isPending || native.isPending || native.isError;
  const statuses = ORDER.map((marketplace) => rowStatus(marketplace, listings[marketplace],
    NATIVE.has(marketplace) ? drafts[marketplace as NativeMarketplace] : undefined, revisionId, etsyEligible));
  const removable = ORDER.filter((marketplace) => deletable(listings[marketplace]));
  const soldSomewhere = ORDER.some((marketplace) => listings[marketplace]?.sold_here);

  const running = (marketplace: Marketplace) => isRunning(drafts[marketplace as NativeMarketplace]);
  const startingOn = (marketplace: Marketplace) => Boolean(starting[marketplace as NativeMarketplace]);
  const actionError = Object.values(actionErrors).filter(Boolean).join(" ");
  const unconfirmed = (marketplace: Marketplace) => NATIVE.has(marketplace)
    && ["saving", "publishing", "updating", "unknown"].includes(drafts[marketplace as NativeMarketplace]?.state ?? "");
  const retryIn = (marketplace: Marketplace) => {
    const draft = drafts[marketplace as NativeMarketplace];
    return draft?.error_code === "rate_limited" ? secondsUntil(draft.retry_after, now) : 0;
  };
  function listable(marketplace: Marketplace, status: Status): boolean {
    if (!NATIVE.has(marketplace) || unconfirmed(marketplace)) return false;
    if (status.tone === "live" || status.label === "Sold here" || status.label === "In review" || listings[marketplace]?.listing_id) return false;
    // A sold item is not listed anywhere else.
    return !soldSomewhere;
  }
  function preflightGaps(marketplace: Marketplace): PreflightGap[] {
    if (preflight.isPlaceholderData || preflight.isError) return [];
    return preflight.data?.marketplaces?.[marketplace as NativeMarketplace]?.gaps ?? [];
  }
  function listBlocked(marketplace: Marketplace): boolean {
    if (!preflight.data || preflight.isPlaceholderData || preflight.isError) return true;
    if (retryIn(marketplace) > 0) return true;
    if (preflightGaps(marketplace).some((gap) => gap.blocking)) return true;
    if (marketplace === "etsy" && !etsyEligible) return true;
    if (marketplace === "etsy") {
      const draft = drafts.etsy;
      const current = draft?.state === "draft" && draft.revision_id === revisionId && Boolean(draft.draft_url);
      return !current && !etsyReady;
    }
    return false;
  }

  /** The marketplace holds an older revision than the one being edited. */
  function outdated(marketplace: Marketplace): boolean {
    const draft = NATIVE.has(marketplace) ? drafts[marketplace as NativeMarketplace] : undefined;
    return draft?.state === "published" && Boolean(draft.revision_id) && draft.revision_id !== revisionId;
  }

  function updatable(marketplace: Marketplace, status: Status): boolean {
    return NATIVE.has(marketplace) && status.tone === "live" && status.label === "Listed" && !unconfirmed(marketplace);
  }

  function actions(marketplace: Marketplace, status: Status): ReactNode {
    const m = marketplace as NativeMarketplace;
    if (running(marketplace)) {
      return <Button size="xs" variant="outline" disabled={startingOn(m)} onClick={() => action.mutate({ marketplace: m, kind: "cancel" })}>Cancel</Button>;
    }
    if (unconfirmed(marketplace)) {
      return (
        <>
          <Button size="xs" variant="ghost" disabled={startingOn(m)} onClick={() => void resetNow(m)}>I checked it myself…</Button>
          <Button size="xs" variant="outline" disabled={startingOn(m)} onClick={() => action.mutate({ marketplace: m, kind: "check" })}>Check result</Button>
        </>
      );
    }
    if (updatable(marketplace, status)) {
      // Nothing to send until the listing changes; Update stays in the row's menu.
      if (!outdated(marketplace)) return null;
      const pending = starting[m] === "update";
      return <Button size="xs" variant="outline" disabled={busy || startingOn(m)} onClick={() => void updateNow(m)}>{pending ? "Updating…" : "Update…"}</Button>;
    }
    if (!listable(marketplace, status)) return null;
    const pending = startingOn(m);
    const wait = retryIn(marketplace);
    return <>
      {REMOTE_DRAFT.has(marketplace) && <Button size="xs" variant="ghost" disabled={busy || pending || listBlocked(marketplace)}
        onClick={() => action.mutate({ marketplace: m, kind: "save_draft" })}>Save draft</Button>}
      <Button size="xs" variant="outline" disabled={busy || pending || listBlocked(marketplace)} onClick={() => void listNow(m)}>
        {pending ? "Working…" : wait > 0 ? `Retry in ${wait}s` : "List…"}</Button>
    </>;
  }

  function rowMenu(marketplace: Marketplace, status: Status): ReactNode {
    const name = marketplaceName(marketplace);
    const m = marketplace as NativeMarketplace;
    const entry = listings[marketplace];
    const canList = listable(marketplace, status);
    const canUpdate = updatable(marketplace, status);
    const canDelist = deletable(entry);
    return (
      <Menu>
        <MenuTrigger render={<Button variant="ghost" size="icon-xs" className="shrink-0" aria-label={`${name} actions`} disabled={busy || startingOn(m)} />}>
          <EllipsisIcon />
        </MenuTrigger>
        <MenuPopup align="end" width="content">
          {canList && <MenuItem disabled={listBlocked(marketplace)} onClick={() => void listNow(m)}>List…</MenuItem>}
          {canList && REMOTE_DRAFT.has(marketplace) && <MenuItem disabled={listBlocked(marketplace)}
            onClick={() => action.mutate({ marketplace: m, kind: "save_draft" })}>Save draft</MenuItem>}
          {canUpdate && <MenuItem onClick={() => void updateNow(m)}>Update…</MenuItem>}
          {canDelist && <MenuItem variant="destructive" onClick={() => void runDelist([marketplace])}>Delist…</MenuItem>}
          {(canList || canUpdate || canDelist) && <MenuSeparator />}
          <MenuItem disabled={running(marketplace)}
            onClick={() => setLinking({ marketplace, url: entry?.source === "seller" ? entry.url ?? "" : "" })}>Mark as listed…</MenuItem>
          {!entry?.sold_here && <MenuItem disabled={running(marketplace)} onClick={() => startSale(marketplace)}>Mark as sold…</MenuItem>}
          {(entry || status.tone === "live" || unconfirmed(marketplace)) && (
            <MenuItem disabled={running(marketplace)} onClick={() => mark.mutate({ marketplace, state: "not_listed" })}>Mark as not listed</MenuItem>
          )}
        </MenuPopup>
      </Menu>
    );
  }

  function preflightNotice(marketplace: Marketplace): ReactNode {
    const draft = NATIVE.has(marketplace) ? drafts[marketplace as NativeMarketplace] : undefined;
    if (draft?.error || isRunning(draft) || unconfirmed(marketplace)) return null;
    const all = preflightGaps(marketplace);
    if (!all.length) return null;
    const name = marketplaceName(marketplace);
    // A gap is said once. One the editor draws a row for is said under that row,
    // where the seller fixes it; the rest are said here, beside List.
    const gaps = saidBesideList(all, [...(fieldsShown[marketplace] ?? [])]);
    const blocking = all.some((gap) => gap.blocking);
    const fields = [...new Set(gaps.map((gap) => gap.field))];
    const prompt = [
      blocking
        ? `${name} still needs details before this item can be listed.`
        : `${name} will leave these fields empty when this item is listed.`,
      ...all.map((gap) => `${gap.field}: ${gap.message}`),
      blocking
        ? "Fill those fields in this listing. Do not list it; I will press List again."
        : `Set each one to the closest of ${name}'s choices in this listing. Do not list it; I will press List again.`,
    ].join("\n");
    return (
      <>
        {uniqueMarketplaceMessages(gaps.map((gap) => gap.message), headerMessages).map((message) => <p key={message} className="text-muted-foreground">{message}</p>)}
        <div className="flex flex-wrap items-center gap-1.5">
          {onEditField && fields.map((field) => (
            <Button key={field} size="xs" variant="ghost" onClick={() => onEditField(field, marketplace)}>
              Edit {fieldLabel(field)}
            </Button>
          ))}
          {onAskChat && (
            <Button size="xs" variant="outline" title="Chat fills these fields. It does not list the item."
              onClick={() => onAskChat(prompt)}>Ask chat</Button>
          )}
        </div>
      </>
    );
  }

  /** The one thing that fixes this refusal, per error code. */
  function errorActions(marketplace: Marketplace): ReactNode {
    const m = marketplace as NativeMarketplace;
    const draft = NATIVE.has(marketplace) ? drafts[m] : undefined;
    if (!draft?.error || isRunning(draft) || draft.state === "unknown") return null;
    const name = marketplaceName(marketplace);
    const code = draft.error_code;
    const buttons: ReactNode[] = [];
    if (code === "signed_out") {
      buttons.push(<Button key="reconnect" size="xs" variant="outline" disabled={startingOn(m)}
        onClick={() => action.mutate({ marketplace: m, kind: "reconnect" })}>Reconnect {name}</Button>);
    }
    if (code === "challenge") {
      buttons.push(<Button key="open" size="xs" variant="outline" disabled={startingOn(m)}
        onClick={() => action.mutate({ marketplace: m, kind: "reconnect" })}>Open {name}</Button>);
    }
    if (code === "missing_fields" && onEditField) {
      for (const item of draft.error_fields ?? []) {
        buttons.push(<Button key={`edit-${item.field}`} size="xs" variant="ghost" title={item.message}
          onClick={() => onEditField(item.field, marketplace)}>Edit {fieldLabel(item.field)}</Button>);
      }
    }
    if ((code === "missing_fields" || code === "rejected" || code === "schema_drift" || !code) && onAskChat) {
      buttons.push(<Button key="ask" size="xs" variant="outline"
        title="Chat fixes the fields in this listing. It does not list it; press List again after."
        onClick={() => onAskChat(askChatPrompt(marketplace, draft))}>Ask chat</Button>);
    }
    return buttons.length ? <div className="flex flex-wrap items-center gap-1.5">{buttons}</div> : null;
  }

  function reconnectNotice(marketplace: Marketplace): ReactNode {
    const session = reconnected[marketplace];
    const text = reconnectText(marketplaceName(marketplace), session);
    if (!text) return null;
    return <p role="status" className={session?.signed_in ? "text-success-foreground" : "text-warning-foreground"}>{text}</p>;
  }

  function linkForm(marketplace: Marketplace): ReactNode {
    if (linking?.marketplace !== marketplace) return null;
    const name = marketplaceName(marketplace);
    const url = linking.url.trim();
    return (
      <form className="flex items-center gap-2" onSubmit={(event) => {
        event.preventDefault();
        if (url) mark.mutate({ marketplace, state: "listed", url });
      }}>
        <Input size="compact" autoFocus aria-label={`${name} listing link`} placeholder={`Paste the ${name} listing link`}
          value={linking.url} onChange={(event) => setLinking({ marketplace, url: event.target.value })} />
        <Button type="submit" size="xs" variant="outline" disabled={busy || !url}>{mark.isPending ? "Saving…" : "Save link"}</Button>
        <Button type="button" size="xs" variant="ghost" onClick={() => { setLinking(null); mark.reset(); }}>Cancel</Button>
      </form>
    );
  }

  function settings(marketplace: Marketplace): ReactNode {
    if (marketplace === "etsy" && etsyCreating && etsyDefaults.isSuccess && !etsyReady) {
      return <p className="text-muted-foreground">Choose Etsy's default shipping, processing, and return profiles in Settings → Connections.</p>;
    }
    if (marketplace === "facebook") return facebookPane();
    return null;
  }

  function facebookPane(): ReactNode {
    const entry = listings.facebook;
    if (!entry?.listing_id || entry.sold_here || entry.state !== "live") {
      return <p className="text-muted-foreground">Anteroom can't list on Facebook yet. If this item is already on Facebook, use Mark as listed with its link.</p>;
    }
    const result = checkFacebook.data;
    const blocked = result && !result.ok && (result.error_code === "signed_out" || result.error_code === "challenge");
    return (
      <>
        <p className="text-muted-foreground">Anteroom can't delete Facebook listings yet. To take it down, delete it on Facebook, then choose Mark as not listed.</p>
        {result && <p role={result.ok ? "status" : "alert"} className={cn(!result.ok && "text-destructive-foreground")}>{facebookCheckText(result)}</p>}
        {result?.marketplace_message && <p className="whitespace-pre-wrap break-words text-muted-foreground">Facebook said: {result.marketplace_message}</p>}
        {checkFacebook.error && <p role="alert" className="text-destructive-foreground">{checkFacebook.error.message}</p>}
        <div className="flex flex-wrap items-center gap-1.5">
          <Button size="xs" variant="outline" disabled={busy || checkFacebook.isPending} title="Reads the listing's page in Chrome. Nothing on Facebook changes."
            onClick={() => checkFacebook.mutate()}>{checkFacebook.isPending ? "Checking…" : "Check on Facebook"}</Button>
          {blocked && <Button size="xs" variant="ghost" disabled={busy}
            onClick={() => void api.marketplaceSessions.connect("facebook")}>Open Facebook</Button>}
        </div>
      </>
    );
  }

  const statusById = new Map(ORDER.map((marketplace, index) => [marketplace, statuses[index]]));
  // List everywhere covers the marketplaces on the rail that are ready now; the rest wait for the seller.
  const unlisted = ORDER.filter((marketplace) => ids.includes(marketplace) && !running(marketplace) && !startingOn(marketplace)
    && listable(marketplace, statusById.get(marketplace)!));
  const readyToList = unlisted.filter((marketplace) => !listBlocked(marketplace)) as NativeMarketplace[];
  const notReady = unlisted.filter((marketplace) => listBlocked(marketplace));
  const selectedMarketplace = MANAGED.has(selected) ? selected as Marketplace : null;
  const selectedStatus = selectedMarketplace ? statusById.get(selectedMarketplace) : undefined;
  const paneError = mark.error?.message || actionError || (delist.error || native.error || live.error)?.message;
  const [detail] = uniqueMarketplaceMessages([selectedStatus?.detail], paneError ? [paneError] : []);
  const [said] = uniqueMarketplaceMessages([selectedStatus?.said], [paneError, detail].filter((message): message is string => Boolean(message)));
  const headerMessages = uniqueMarketplaceMessages([paneError, detail, said]);
  function visiblePreflightGaps(marketplace: Marketplace): PreflightGap[] {
    const draft = NATIVE.has(marketplace) ? drafts[marketplace as NativeMarketplace] : undefined;
    return draft?.error || isRunning(draft) || unconfirmed(marketplace) ? [] : saidBesideList(preflightGaps(marketplace), [...(fieldsShown[marketplace] ?? [])]);
  }
  const shownMessages = uniqueMarketplaceMessages([
    ...headerMessages,
    ...(selectedMarketplace ? visiblePreflightGaps(selectedMarketplace).map((gap) => gap.message) : []),
  ]);

  return (
    <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden">
      <nav aria-label="Marketplaces" className="flex w-40 shrink-0 flex-col gap-0.5 overflow-y-auto border-e py-1.5 ps-1.5 pe-1">
        <ul className="flex flex-col gap-0.5 text-xs">
          {ids.map((id) => {
            const marketplace = MANAGED.has(id) ? id as Marketplace : null;
            const status = marketplace ? statusById.get(marketplace) : undefined;
            const name = railLabel(id);
            const current = selected === id;
            // "Not listed" is the ordinary state. Saying it on every row hides
            // the few that actually need a look (unconfirmed, ineligible, live).
            const quiet = status?.tone === "idle";
            return (
              <li key={id} aria-label={name} className="group relative flex items-start">
                <button type="button" aria-selected={current} title={status?.label} onClick={() => onSelect(id)}
                  className={cn("flex min-w-0 flex-1 items-center gap-1.5 rounded-md px-1.5 py-1.5 pe-6 text-left hover:bg-accent",
                    current && "bg-accent shadow-[inset_2px_0_0_var(--primary)]")}>
                  {marketplace ? <MarketplaceLogo id={marketplace} label={name} size={16} /> : <span aria-hidden className="size-4 shrink-0" />}
                  <span className="min-w-0">
                    <span className="block truncate font-medium text-foreground">{name}</span>
                    {status && !quiet && (
                      <span role="status" className={cn("flex items-center gap-1 truncate text-xs text-muted-foreground",
                        status.tone === "failed" && "text-destructive-foreground", status.tone === "attention" && "text-warning-foreground")}>
                        {TONE_ICON[status.tone]}
                        <span className="truncate">{status.label}</span>
                      </span>
                    )}
                    {status && quiet && <span className="sr-only">{status.label}</span>}
                  </span>
                </button>
                {marketplace && status ? (
                  <span className={cn(
                    "absolute end-0.5 top-0.5 opacity-0 pointer-events-none group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100",
                    current && "pointer-events-auto opacity-100",
                  )}>
                    {rowMenu(marketplace, status)}
                  </span>
                ) : null}
              </li>
            );
          })}
        </ul>
        {/* Listing-wide actions sit apart from the marketplaces, pinned to the
            rail's foot: quiet rows that match it, the destructive one last. */}
        <div className="mt-auto flex flex-col gap-0.5 border-t pt-1.5 [&>[data-slot=button]]:w-full [&>[data-slot=button]]:justify-start">
          {railActions}
          {readyToList.length > 1 && (
            <Button size="xs" variant="ghost" disabled={busy} title={`List on ${readyToList.map(marketplaceName).join(", ")}`}
              onClick={() => void listEverywhere(readyToList, notReady)}><SendIcon />List everywhere…</Button>
          )}
          <Button size="xs" variant="ghost-muted" disabled={busy} title="A sale in person or anywhere off these marketplaces"
            onClick={() => startSale("other")}><ReceiptIcon />Record a sale…</Button>
          {removable.length > 1 && (
            <Button size="xs" variant="ghost-destructive" disabled={busy} onClick={() => void runDelist(removable)}>
              <Trash2Icon />{delist.isPending ? "Delisting…" : "Delist everywhere…"}
            </Button>
          )}
          {railStatus && <div className="mt-1 flex min-h-6 items-center gap-1.5 border-t ps-2 pt-1">{railStatus}</div>}
        </div>
      </nav>
      {/* Content stops at a readable line length; the pane still scrolls edge to edge. */}
      <div className="marketplace-pane flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-y-auto px-3 py-3 *:w-full *:max-w-3xl">
        {paneError && (
          <p role="alert" className="text-destructive-foreground text-xs">{paneError}</p>
        )}
        {selling && <MarkSoldForm key={selling.key} initial={selling.marketplace} pending={recordSale.isPending}
          error={recordSale.error?.message} onSubmit={(sale) => recordSale.mutate(sale)} onCancel={() => setSelling(null)} />}
        {selectedMarketplace && selectedStatus && (
          <section data-marketplace-pane={selectedMarketplace} className="flex flex-col gap-2 rounded-lg bg-muted/40 px-2.5 py-2 text-xs">
            {/* Name on the left, every action on the right. A listed item's state is
                already on its rail row, so the bar only spells out the others. */}
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="flex min-w-0 items-center gap-1.5">
                <MarketplaceLogo id={selectedMarketplace} label={railLabel(selectedMarketplace)} size={16} />
                <span className="truncate font-medium text-foreground text-sm">{railLabel(selectedMarketplace)}</span>
                {selectedStatus.tone !== "live" && (
                  <span className={cn("flex min-w-0 items-center gap-1 text-muted-foreground", selectedStatus.tone === "failed" && "text-destructive-foreground", selectedStatus.tone === "attention" && "text-warning-foreground")}>
                    {selectedStatus.tone !== "idle" && TONE_ICON[selectedStatus.tone]}
                    <span className="truncate">{selectedStatus.tone === "idle" ? "Not listed yet" : selectedStatus.label}</span>
                  </span>
                )}
              </span>
              <span className="ms-auto flex shrink-0 items-center gap-1.5">
                {selectedStatus.href && (
                  <Button size="xs" variant="ghost" aria-label={`${selectedStatus.hrefLabel} on ${railLabel(selectedMarketplace)}`}
                    render={<a href={selectedStatus.href} target="_blank" rel="noopener noreferrer" />}>
                    {selectedStatus.hrefLabel}<ExternalLinkIcon aria-hidden />
                  </Button>
                )}
                {actions(selectedMarketplace, selectedStatus)}
              </span>
            </div>
            {detail && <p className={cn(selectedStatus.tone === "failed" ? "text-destructive-foreground" : "text-muted-foreground")}
              role={selectedStatus.tone === "failed" ? "alert" : undefined}>{detail}</p>}
            {said && <p className="whitespace-pre-wrap break-words text-muted-foreground" data-marketplace-message>{said}</p>}
            {reconnectNotice(selectedMarketplace)}
            {errorActions(selectedMarketplace)}
            {preflightNotice(selectedMarketplace)}
            {settings(selectedMarketplace)}
            {linkForm(selectedMarketplace)}
            {(() => {
              const draft = NATIVE.has(selectedMarketplace) ? drafts[selectedMarketplace as NativeMarketplace] : undefined;
              const name = railLabel(selectedMarketplace);
              if (draft?.state !== "unknown" || draft.draft_url || selectedMarketplace === "poshmark" || selectedMarketplace === "sellwild" || selectedMarketplace === "ebay") return null;
              return (
                <div className="flex items-center gap-2">
                  <Input size="compact" aria-label={`Saved ${name} link`} placeholder="Paste the link if one was created"
                    value={draftLinks[selectedMarketplace as NativeMarketplace] || ""}
                    onChange={(event) => setDraftLinks({ ...draftLinks, [selectedMarketplace]: event.target.value })} />
                  <Button size="xs" variant="outline" disabled={startingOn(selectedMarketplace) || !draftLinks[selectedMarketplace as NativeMarketplace]?.trim()}
                    onClick={() => action.mutate({ marketplace: selectedMarketplace as NativeMarketplace, kind: "check", draftUrl: draftLinks[selectedMarketplace as NativeMarketplace]?.trim() })}>Check link</Button>
                </div>
              );
            })()}
          </section>
        )}
        <FieldsShownContext.Provider value={reportFields}>
          <MarketplaceMessagesContext value={shownMessages}>{children}</MarketplaceMessagesContext>
        </FieldsShownContext.Provider>
      </div>
    </div>
  );
}

/** The marketplace rail and the selected marketplace's actions, used on its own in tests. */
export function MarketplacesSection(props: MarketplaceProps & { selected?: string }) {
  return (
    <MarketplacesLayout {...props} ids={ORDER} selected={props.selected ?? ""} onSelect={() => {}}>
      {null}
    </MarketplacesLayout>
  );
}
