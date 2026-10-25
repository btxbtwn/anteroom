import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangleIcon, CheckCircle2Icon, CircleIcon, EllipsisIcon, ExternalLinkIcon, MinusCircleIcon, XCircleIcon } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { DelistMarketplace, MarketplaceListing, MarketplaceListings, MarketplaceMark } from "../api/marketplaceListings";
import type { NativeDraft, NativeDrafts, NativeMarketplace, PreflightGap, SellwildOptions } from "../api/nativeMarketplaces";
import { EXTENSION_SESSION_MARKETS, type ExtensionSessionMarket } from "../api/marketplaceSessions";
import { cn } from "../lib/utils";
import { confirmDialog } from "../ui/confirmDialog";
import { MarketplaceLogo } from "./MarketplaceLogo";
import { marketplaceName } from "./marketplaceNames";
import { Button } from "./ui/button";
import { Combobox, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList, ComboboxPopup } from "./ui/combobox";
import { Input } from "./ui/input";
import { Menu, MenuItem, MenuPopup, MenuSeparator, MenuTrigger } from "./ui/menu";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { Spinner } from "./ui/spinner";

type Marketplace = DelistMarketplace;
const ORDER: Marketplace[] = ["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild"];
const NATIVE = new Set<Marketplace>(["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild"]);
// What Delist does on each marketplace: eBay has no delete; Sellwild offers only its own delist.
const REMOVED: Record<Marketplace, string> = {
  ebay: "Ended", etsy: "Deleted", poshmark: "Deleted", mercari: "Deleted", depop: "Deleted", sellwild: "Delisted",
};

type Tone = "live" | "neutral" | "busy" | "attention" | "failed" | "idle";
type Status = { tone: Tone; label: string; href?: string; hrefLabel?: string; detail?: string | null; said?: string | null };

const BUSY = new Set(["saving", "publishing", "updating"]);
export const ETSY_NOT_ELIGIBLE = "Etsy only allows vintage (20+ years), handmade or craft supplies. This item doesn't qualify.";

/** A record whose job is running now. A busy state with no job running is unconfirmed until checked. */
export function isRunning(draft: NativeDraft | undefined): boolean {
  return Boolean(draft?.running) || (BUSY.has(draft?.state ?? "") && draft?.running === undefined);
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
  return Boolean(entry?.listing_id && !entry.sold_here && entry.state !== "deleted" && entry.state !== "deleting");
}

/** One status per marketplace. A live listing wins. Anything else is not listed; Anteroom holds the listing. */
export function rowStatus(marketplace: Marketplace, live: MarketplaceListing | undefined, draft: NativeDraft | undefined,
  _revisionId: string | null, etsyEligible: boolean): Status {
  if (live?.sold_here) return { tone: "neutral", label: "Sold here", href: live.url ?? undefined, hrefLabel: "View" };
  const said = draft?.marketplace_message ? `${marketplaceName(marketplace)} said: ${draft.marketplace_message}` : null;
  if (draft && isRunning(draft)) {
    return { tone: "busy", label: draft.state === "updating" ? "Updating…" : "Listing…", detail: draft.progress ? `${draft.progress.label}…` : null };
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
  if (live?.listing_id) {
    return { tone: "live", label: "Live", href: live.url ?? undefined, hrefLabel: "View", detail: draft?.state === "published" ? draft.error : null, said };
  }
  if (draft && draft.state !== "draft") {
    if (draft.state === "published") return { tone: "live", label: "Live", href: draft.listing_url, hrefLabel: "View" };
    if (draft.state === "pending_review") return { tone: "attention", label: "In review", href: draft.listing_url, hrefLabel: "View" };
    if (draft.error) return { tone: "failed", label: "Not listed", detail: draft.error, said };
  }
  if (draft?.state === "draft" && draft.error) return { tone: "failed", label: "Not listed", detail: draft.error, said };
  if (marketplace === "etsy" && !etsyEligible) return { tone: "attention", label: "Not eligible", detail: ETSY_NOT_ELIGIBLE };
  return { tone: "idle", label: "Not listed" };
}

type Option = { id: number; label: string };

function railLabel(id: string): string {
  if (id === "general") return "General";
  return marketplaceName(id);
}

const MANAGED = new Set<string>(ORDER);

function SellwildCategory({ options, value, onChange, disabled }: {
  options: SellwildOptions["categories"]; value: number | undefined; onChange: (id: number | undefined) => void; disabled: boolean;
}) {
  const selected = options.find((option) => option.id === value) ?? null;
  return (
    <Combobox<Option> items={options} value={selected} disabled={disabled}
      itemToStringLabel={(option) => option.label}
      isItemEqualToValue={(a, b) => a.id === b.id}
      onValueChange={(option) => onChange(option?.id)}>
      <ComboboxInput size="sm" aria-label="Sellwild category" placeholder="Search Sellwild categories" />
      <ComboboxPopup>
        <ComboboxEmpty>No matching category.</ComboboxEmpty>
        <ComboboxList>
          {(option: Option) => <ComboboxItem key={option.id} value={option}>{option.label}</ComboboxItem>}
        </ComboboxList>
      </ComboboxPopup>
    </Combobox>
  );
}

type MarketplaceProps = {
  convId: string; revisionId: string | null; title: string; disabled: boolean; etsyEligible: boolean;
  /** Send a prompt to the listing's chat; absent while chat is busy. */
  onAskChat?: (text: string) => void;
  /** Open the editor at a listing field, e.g. "price" or "ebay_specifics.category_specifics.Color". */
  onEditField?: (field: string) => void;
};

export function MarketplacesLayout({
  convId, revisionId, title, disabled, etsyEligible, onAskChat, onEditField, ids, selected, onSelect, children,
}: MarketplaceProps & {
  ids: readonly string[];
  selected: string;
  onSelect: (id: string) => void;
  children?: ReactNode;
}) {
  const queryClient = useQueryClient();
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
  const [changingSellwild, setChangingSellwild] = useState(false);
  const [sellwildPick, setSellwildPick] = useState<{ categoryId?: number; conditionId?: number }>({});
  const [linking, setLinking] = useState<{ marketplace: Marketplace; url: string } | null>(null);

  const etsyCreating = !listings.etsy?.listing_id && !drafts.etsy?.draft_url;
  const etsyDefaults = useQuery({ queryKey: ["etsy-listing-defaults"], queryFn: () => api.etsy.listingDefaults(),
    enabled: etsyCreating, staleTime: 60_000, retry: false });
  const etsyReady = Boolean(etsyDefaults.data?.shipping_profile_id && etsyDefaults.data?.readiness_state_id && etsyDefaults.data?.return_policy_id);
  const sellwildOpen = !listings.sellwild?.listing_id && !["published", "pending_review"].includes(drafts.sellwild?.state ?? "");
  const sellwildOptions = useQuery({ queryKey: ["sellwild-options", convId, revisionId], queryFn: () => api.nativeMarketplaces.sellwildOptions(convId),
    enabled: sellwildOpen, staleTime: 60_000, retry: false });
  const savedSellwild = drafts.sellwild;
  const sellwildCategory = sellwildPick.categoryId ?? savedSellwild?.sellwild_category_id ?? sellwildOptions.data?.suggested.category_id ?? undefined;
  const sellwildCondition = sellwildPick.conditionId ?? savedSellwild?.sellwild_condition_id ?? sellwildOptions.data?.suggested.condition_id ?? undefined;
  const preflight = useQuery({
    queryKey: ["native-preflight", convId, revisionId, sellwildCategory ?? null, sellwildCondition ?? null],
    queryFn: () => api.nativeMarketplaces.preflight(convId, { categoryId: sellwildCategory, conditionId: sellwildCondition }),
    enabled: Boolean(revisionId),
    staleTime: 15_000,
  });

  const action = useMutation({
    mutationFn: async ({ marketplace, kind, draftUrl }: {
      marketplace: NativeMarketplace; kind: "check" | "list" | "update" | "cancel" | "reset" | "reconnect"; draftUrl?: string;
    }) => {
      if (kind === "check") return api.nativeMarketplaces.check(convId, marketplace, draftUrl);
      if (kind === "cancel") return api.nativeMarketplaces.cancel(convId, marketplace);
      if (kind === "reset") return api.nativeMarketplaces.reset(convId, marketplace);
      if (kind === "reconnect") {
        if (marketplace === "ebay" || marketplace === "etsy") await api.directConnections.connect(marketplace);
        else if ((EXTENSION_SESSION_MARKETS as readonly string[]).includes(marketplace)) await api.marketplaceSessions.connect(marketplace as ExtensionSessionMarket);
        return drafts[marketplace];
      }
      if (!revisionId) throw new Error("Generate a listing first.");
      if (kind === "update") return api.nativeMarketplaces.update(convId, marketplace, revisionId);
      return api.nativeMarketplaces.list(convId, marketplace, revisionId,
        marketplace === "sellwild" && sellwildCategory && sellwildCondition
          ? { categoryId: sellwildCategory, conditionId: sellwildCondition } : undefined);
    },
    retry: false,
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: nativeKey });
      void queryClient.invalidateQueries({ queryKey: liveKey });
      void queryClient.invalidateQueries({ queryKey: ["jobs", convId] });
    },
  });
  const delist = useMutation({
    mutationFn: (marketplaces: Marketplace[]) => api.marketplaceListings.delist(convId, marketplaces),
    retry: false,
    onSuccess: (data) => queryClient.setQueryData(liveKey, data),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: liveKey }),
  });
  const mark = useMutation({
    mutationFn: ({ marketplace, state, url }: { marketplace: Marketplace; state: MarketplaceMark; url?: string }) =>
      api.marketplaceListings.mark(convId, marketplace, state, url),
    retry: false,
    onSuccess: (data) => { queryClient.setQueryData(liveKey, data); setLinking(null); },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: liveKey });
      void queryClient.invalidateQueries({ queryKey: nativeKey });
    },
  });

  const UPDATABLE = new Set<Marketplace>(["ebay", "etsy", "poshmark"]);

  async function updateNow(marketplace: NativeMarketplace) {
    const name = marketplaceName(marketplace);
    if (await confirmDialog(`Update the live ${name} listing for “${title}” with what's saved in Anteroom?\nThis changes the listing that is already up.`,
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
  async function runDelist(marketplaces: Marketplace[]) {
    const names = marketplaces.map(marketplaceName);
    const lines = marketplaces.map((marketplace) => `• ${marketplaceName(marketplace)}: ${REMOVED[marketplace].toLowerCase()}`).join("\n");
    const approved = await confirmDialog(`Delist “${title}” from ${names.length === 1 ? names[0] : `${names.length} marketplaces`}?\n${lines}\nA deleted listing can't be restored; listing again creates a new one.`,
      { variant: "destructive", confirmLabel: names.length === 1 ? `Delist from ${names[0]}` : `Delist from ${names.length}` });
    if (approved) delist.mutate(marketplaces);
  }

  async function resetNow(marketplace: NativeMarketplace) {
    const name = marketplaceName(marketplace);
    if (await confirmDialog(`Did you check ${name} yourself?\nAnteroom will treat “${title}” as not listed on ${name}, so you can List again. Nothing on ${name} changes. If the listing is there, use Mark as listed instead.`,
      { confirmLabel: "I checked it myself" })) action.mutate({ marketplace, kind: "reset" });
  }

  const busy = disabled || action.isPending || delist.isPending || mark.isPending || native.isPending || native.isError;
  const statuses = ORDER.map((marketplace) => rowStatus(marketplace, listings[marketplace],
    NATIVE.has(marketplace) ? drafts[marketplace as NativeMarketplace] : undefined, revisionId, etsyEligible));
  const removable = ORDER.filter((marketplace) => deletable(listings[marketplace]));
  const soldSomewhere = ORDER.some((marketplace) => listings[marketplace]?.sold_here);

  const running = (marketplace: Marketplace) => isRunning(drafts[marketplace as NativeMarketplace]);
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
    return preflight.data?.marketplaces?.[marketplace as NativeMarketplace]?.gaps ?? [];
  }
  function listBlocked(marketplace: Marketplace): boolean {
    if (retryIn(marketplace) > 0) return true;
    if (preflightGaps(marketplace).some((gap) => gap.blocking)) return true;
    if (marketplace === "etsy" && !etsyEligible) return true;
    if (marketplace === "etsy") {
      const draft = drafts.etsy;
      const current = draft?.state === "draft" && draft.revision_id === revisionId && Boolean(draft.draft_url);
      return !current && !etsyReady;
    }
    return marketplace === "sellwild" && (!sellwildCategory || !sellwildCondition);
  }

  function updatable(marketplace: Marketplace, status: Status): boolean {
    return UPDATABLE.has(marketplace) && status.tone === "live" && status.label === "Live" && !unconfirmed(marketplace);
  }

  function actions(marketplace: Marketplace, status: Status): ReactNode {
    const m = marketplace as NativeMarketplace;
    if (running(marketplace)) {
      return <Button size="xs" variant="outline" disabled={action.isPending} onClick={() => action.mutate({ marketplace: m, kind: "cancel" })}>Cancel</Button>;
    }
    if (unconfirmed(marketplace)) {
      return (
        <>
          <Button size="xs" variant="ghost" disabled={action.isPending} onClick={() => void resetNow(m)}>I checked it myself…</Button>
          <Button size="xs" variant="outline" disabled={action.isPending} onClick={() => action.mutate({ marketplace: m, kind: "check" })}>Check result</Button>
        </>
      );
    }
    if (updatable(marketplace, status)) {
      const pending = action.isPending && action.variables?.marketplace === m && action.variables?.kind === "update";
      return <Button size="xs" variant="outline" disabled={busy} onClick={() => void updateNow(m)}>{pending ? "Updating…" : "Update…"}</Button>;
    }
    if (!listable(marketplace, status)) return null;
    const pending = action.isPending && action.variables?.marketplace === m;
    const wait = retryIn(marketplace);
    return <Button size="xs" variant="outline" disabled={busy || listBlocked(marketplace)} onClick={() => void listNow(m)}>
      {pending ? "Listing…" : wait > 0 ? `Retry in ${wait}s` : "List…"}</Button>;
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
        <MenuTrigger render={<Button variant="ghost" size="icon-xs" className="shrink-0" aria-label={`${name} actions`} disabled={busy} />}>
          <EllipsisIcon />
        </MenuTrigger>
        <MenuPopup align="end" width="content">
          {canList && <MenuItem disabled={listBlocked(marketplace)} onClick={() => void listNow(m)}>List…</MenuItem>}
          {canUpdate && <MenuItem onClick={() => void updateNow(m)}>Update…</MenuItem>}
          {canDelist && <MenuItem variant="destructive" onClick={() => void runDelist([marketplace])}>Delist…</MenuItem>}
          {(canList || canUpdate || canDelist) && <MenuSeparator />}
          <MenuItem disabled={running(marketplace)}
            onClick={() => setLinking({ marketplace, url: entry?.source === "seller" ? entry.url ?? "" : "" })}>Mark as listed…</MenuItem>
          {!entry?.sold_here && <MenuItem disabled={running(marketplace)} onClick={() => mark.mutate({ marketplace, state: "sold" })}>Mark as sold</MenuItem>}
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
    const gaps = preflightGaps(marketplace);
    if (!gaps.length) return null;
    const name = marketplaceName(marketplace);
    const blocking = gaps.some((gap) => gap.blocking);
    const fields = [...new Set(gaps.map((gap) => gap.field))];
    const prompt = [
      blocking
        ? `${name} still needs details before this item can be listed.`
        : `${name} will leave these fields empty when this item is listed.`,
      ...gaps.map((gap) => `${gap.field}: ${gap.message}`),
      blocking
        ? "Fill those fields in this listing. Do not list it; I will press List again."
        : `Set each one to the closest of ${name}'s choices in this listing. Do not list it; I will press List again.`,
    ].join("\n");
    return (
      <>
        {gaps.map((gap) => <p key={`${gap.field}:${gap.message}`} className="text-muted-foreground">{gap.message}</p>)}
        <div className="flex flex-wrap items-center gap-1.5">
          {onEditField && fields.map((field) => (
            <Button key={field} size="xs" variant="ghost" onClick={() => onEditField(field)}>
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
      buttons.push(<Button key="reconnect" size="xs" variant="outline" disabled={action.isPending}
        onClick={() => action.mutate({ marketplace: m, kind: "reconnect" })}>Reconnect {name}</Button>);
    }
    if (code === "challenge") {
      buttons.push(<Button key="open" size="xs" variant="outline" disabled={action.isPending}
        onClick={() => action.mutate({ marketplace: m, kind: "reconnect" })}>Open {name}</Button>);
    }
    if (code === "missing_fields" && onEditField) {
      for (const item of draft.error_fields ?? []) {
        buttons.push(<Button key={`edit-${item.field}`} size="xs" variant="ghost" title={item.message}
          onClick={() => onEditField(item.field)}>Edit {fieldLabel(item.field)}</Button>);
      }
    }
    if ((code === "missing_fields" || code === "rejected") && onAskChat) {
      buttons.push(<Button key="ask" size="xs" variant="outline"
        title="Chat fixes the fields in this listing. It does not list it; press List again after."
        onClick={() => onAskChat(askChatPrompt(marketplace, draft))}>Ask chat</Button>);
    }
    return buttons.length ? <div className="flex flex-wrap items-center gap-1.5">{buttons}</div> : null;
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
    if (marketplace !== "sellwild" || !sellwildOpen || !sellwildOptions.data) return null;
    const category = sellwildOptions.data.categories.find((option) => option.id === sellwildCategory);
    const condition = sellwildOptions.data.conditions.find((option) => option.id === sellwildCondition);
    if (category && condition && !changingSellwild) {
      return (
        <p className="text-muted-foreground">
          {category.label} · {condition.label}
          <button type="button" className="ms-2 text-foreground/80 hover:text-foreground hover:underline" disabled={busy}
            onClick={() => setChangingSellwild(true)}>Change</button>
        </p>
      );
    }
    return (
      <>
        <div className="flex flex-wrap items-center gap-2">
          <div className="min-w-40 flex-1 basis-0"><SellwildCategory options={sellwildOptions.data.categories} value={sellwildCategory} disabled={busy}
            onChange={(categoryId) => setSellwildPick({ ...sellwildPick, categoryId })} /></div>
          <Select value={sellwildCondition ? String(sellwildCondition) : null} disabled={busy}
            onValueChange={(value) => setSellwildPick({ ...sellwildPick, conditionId: value ? Number(value) : undefined })}
            items={sellwildOptions.data.conditions.map((option) => ({ value: String(option.id), label: option.label }))}>
            <SelectTrigger size="compact" className="w-36" aria-label="Sellwild condition"><SelectValue placeholder="Condition" /></SelectTrigger>
            <SelectPopup>{sellwildOptions.data.conditions.map((option) => <SelectItem key={option.id} value={String(option.id)}>{option.label}</SelectItem>)}</SelectPopup>
          </Select>
        </div>
        {(!category || !condition) && <p className="text-muted-foreground">Anteroom couldn't match this item to Sellwild. Choose a category and condition.</p>}
      </>
    );
  }

  const statusById = new Map(ORDER.map((marketplace, index) => [marketplace, statuses[index]]));
  const selectedMarketplace = MANAGED.has(selected) ? selected as Marketplace : null;
  const selectedStatus = selectedMarketplace ? statusById.get(selectedMarketplace) : undefined;

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
                      <span role="status" className={cn("flex items-center gap-1 truncate text-[10px] text-muted-foreground",
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
        {removable.length > 1 && (
          <Button size="xs" variant="destructive-outline" className="mt-1" disabled={busy} onClick={() => void runDelist(removable)}>
            {delist.isPending ? "Delisting…" : "Delist everywhere…"}
          </Button>
        )}
      </nav>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-y-auto px-3 py-3">
        {(native.error || live.error || action.error || delist.error || mark.error) && (
          <p role="alert" className="text-destructive-foreground text-xs">{(mark.error || action.error || delist.error || native.error || live.error)?.message}</p>
        )}
        {selectedMarketplace && selectedStatus && (
          <section data-marketplace-pane={selectedMarketplace} className="flex flex-col gap-2 rounded-lg bg-muted/40 px-2.5 py-2 text-xs">
            <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
              <span className="flex min-w-0 items-center gap-1.5 text-muted-foreground">
                {selectedStatus.tone !== "idle" && TONE_ICON[selectedStatus.tone]}
                <span className={cn("truncate", selectedStatus.tone === "failed" && "text-destructive-foreground", selectedStatus.tone === "attention" && "text-warning-foreground")}>
                  {selectedStatus.tone === "idle" ? `Not on ${railLabel(selectedMarketplace)} yet` : selectedStatus.label}
                </span>
                {selectedStatus.href && <a className="inline-flex items-center gap-0.5 text-foreground/80 hover:text-foreground hover:underline" href={selectedStatus.href}
                  target="_blank" rel="noopener noreferrer" aria-label={`${selectedStatus.hrefLabel} on ${railLabel(selectedMarketplace)}`}>{selectedStatus.hrefLabel}<ExternalLinkIcon aria-hidden className="size-3" /></a>}
              </span>
              <span className="ms-auto flex shrink-0 items-center gap-1.5">{actions(selectedMarketplace, selectedStatus)}</span>
            </div>
            {selectedStatus.detail && <p className={cn(selectedStatus.tone === "failed" ? "text-destructive-foreground" : "text-muted-foreground")}
              role={selectedStatus.tone === "failed" ? "alert" : undefined}>{selectedStatus.detail}</p>}
            {selectedStatus.said && <p className="whitespace-pre-wrap break-words text-muted-foreground" data-marketplace-message>{selectedStatus.said}</p>}
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
                  <Button size="xs" variant="outline" disabled={action.isPending || !draftLinks[selectedMarketplace as NativeMarketplace]?.trim()}
                    onClick={() => action.mutate({ marketplace: selectedMarketplace as NativeMarketplace, kind: "check", draftUrl: draftLinks[selectedMarketplace as NativeMarketplace]?.trim() })}>Check link</Button>
                </div>
              );
            })()}
          </section>
        )}
        {children}
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
