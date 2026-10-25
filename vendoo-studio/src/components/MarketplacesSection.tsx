import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangleIcon, CheckCircle2Icon, CircleIcon, EllipsisIcon, ExternalLinkIcon, MinusCircleIcon, XCircleIcon } from "lucide-react";
import { type ReactNode, useState } from "react";
import { api } from "../api/client";
import type { DelistMarketplace, MarketplaceListing, MarketplaceListings, MarketplaceMark } from "../api/marketplaceListings";
import type { NativeDraft, NativeDrafts, NativeMarketplace, SellwildOptions } from "../api/nativeMarketplaces";
import { cn } from "../lib/utils";
import { confirmDialog } from "../ui/confirmDialog";
import { InspectorSection } from "./ListingFormControls";
import { marketplaceName } from "./marketplaceNames";
import { Button } from "./ui/button";
import { Combobox, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList, ComboboxPopup } from "./ui/combobox";
import { Input } from "./ui/input";
import { Menu, MenuItem, MenuPopup, MenuSeparator, MenuTrigger } from "./ui/menu";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { Spinner } from "./ui/spinner";

type Marketplace = DelistMarketplace;
const ORDER: Marketplace[] = ["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild"];
const NATIVE = new Set<Marketplace>(["etsy", "poshmark", "mercari", "depop", "sellwild"]);
// Marketplaces that list in one confirmed step, with no marketplace draft.
const ONE_STEP = new Set<Marketplace>(["poshmark"]);
// What Delist does on each marketplace: eBay has no delete; Sellwild offers only its own delist.
const REMOVED: Record<Marketplace, string> = {
  ebay: "Ended", etsy: "Deleted", poshmark: "Deleted", mercari: "Deleted", depop: "Deleted", sellwild: "Delisted",
};

type Tone = "live" | "neutral" | "busy" | "attention" | "failed" | "idle";
type Status = { tone: Tone; label: string; href?: string; hrefLabel?: string; detail?: string | null };

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

/** One status per marketplace, from its live listing first and Anteroom's own draft second. */
export function rowStatus(marketplace: Marketplace, live: MarketplaceListing | undefined, draft: NativeDraft | undefined,
  revisionId: string | null, etsyEligible: boolean): Status {
  if (live?.sold_here) return { tone: "neutral", label: "Sold here", href: live.url ?? undefined, hrefLabel: "View" };
  if (live?.state === "deleting") return { tone: "busy", label: "Delisting…" };
  if (live?.state === "deleted") return { tone: "neutral", label: REMOVED[marketplace] };
  if (live?.state === "failed") return { tone: "failed", label: "Not delisted", href: live.url ?? undefined, hrefLabel: "View", detail: live.error };
  if (live?.listing_id) return { tone: "live", label: "Live", href: live.url ?? undefined, hrefLabel: "View" };
  // One-step marketplaces have no drafts; an older saved draft no longer counts.
  if (draft && !(ONE_STEP.has(marketplace) && draft.state === "draft")) {
    if (draft.state === "saving") return { tone: "busy", label: "Saving…" };
    if (draft.state === "publishing") return { tone: "busy", label: ONE_STEP.has(marketplace) ? "Listing…" : "Publishing…" };
    if (draft.state === "unknown") return { tone: "attention", label: "Unconfirmed", detail: draft.error || "Check the result before trying again." };
    if (draft.state === "published") return { tone: "live", label: "Live", href: draft.listing_url, hrefLabel: "View" };
    if (draft.state === "pending_review") return { tone: "attention", label: "In review", href: draft.listing_url, hrefLabel: "View" };
    if (draft.state === "draft") {
      const changed = draft.revision_id !== revisionId;
      return { tone: changed ? "attention" : "idle", label: changed ? "Draft out of date" : draft.storage === "local" ? "Saved in Anteroom" : "Draft saved",
        href: draft.draft_url, hrefLabel: "Open draft", detail: draft.error };
    }
    if (draft.error) return { tone: "failed", label: "Not listed", detail: draft.error };
  }
  if (marketplace === "etsy" && !etsyEligible) return { tone: "neutral", label: "Not eligible", detail: "Etsy takes only vintage (20+ years), handmade, craft-supply or digital items." };
  return { tone: "idle", label: "Not listed" };
}

function summary(rows: Status[]): string {
  const live = rows.filter((row) => row.tone === "live").length;
  const sold = rows.filter((row) => row.label === "Sold here").length;
  const attention = rows.filter((row) => row.tone === "attention" || row.tone === "failed").length;
  return [live && `${live} live`, sold && `${sold} sold`, attention && `${attention} need attention`].filter(Boolean).join(" · ");
}

type Option = { id: number; label: string };

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

export function MarketplacesSection({ convId, revisionId, title, disabled, etsyEligible }: {
  convId: string; revisionId: string | null; title: string; disabled: boolean; etsyEligible: boolean;
}) {
  const queryClient = useQueryClient();
  const nativeKey = ["native-marketplaces", convId];
  const liveKey = ["marketplace-listings", convId];
  const native = useQuery({ queryKey: nativeKey, queryFn: () => api.nativeMarketplaces.status(convId) });
  const live = useQuery({ queryKey: liveKey, queryFn: () => api.marketplaceListings.get(convId) });
  const drafts: NativeDrafts = native.data ?? {};
  const listings: MarketplaceListings = live.data ?? {};
  const [draftLinks, setDraftLinks] = useState<Partial<Record<NativeMarketplace, string>>>({});
  const [changingSellwild, setChangingSellwild] = useState(false);
  const [sellwildPick, setSellwildPick] = useState<{ categoryId?: number; conditionId?: number }>({});
  const [linking, setLinking] = useState<{ marketplace: Marketplace; url: string } | null>(null);

  const etsyCreating = etsyEligible && !listings.etsy?.listing_id && !drafts.etsy?.draft_url;
  const etsyDefaults = useQuery({ queryKey: ["etsy-listing-defaults"], queryFn: () => api.etsy.listingDefaults(),
    enabled: etsyCreating, staleTime: 60_000, retry: false });
  const etsyReady = Boolean(etsyDefaults.data?.shipping_profile_id && etsyDefaults.data?.readiness_state_id);
  const sellwildOpen = !listings.sellwild?.listing_id && !["published", "pending_review"].includes(drafts.sellwild?.state ?? "");
  const sellwildOptions = useQuery({ queryKey: ["sellwild-options", convId, revisionId], queryFn: () => api.nativeMarketplaces.sellwildOptions(convId),
    enabled: sellwildOpen, staleTime: 60_000, retry: false });
  const savedSellwild = drafts.sellwild;
  const sellwildCategory = sellwildPick.categoryId ?? savedSellwild?.sellwild_category_id ?? sellwildOptions.data?.suggested.category_id ?? undefined;
  const sellwildCondition = sellwildPick.conditionId ?? savedSellwild?.sellwild_condition_id ?? sellwildOptions.data?.suggested.condition_id ?? undefined;
  const sellwildChoiceChanged = savedSellwild?.state === "draft"
    && (savedSellwild.sellwild_category_id !== sellwildCategory || savedSellwild.sellwild_condition_id !== sellwildCondition);

  const action = useMutation({
    mutationFn: async ({ marketplace, kind, draftUrl }: { marketplace: NativeMarketplace; kind: "draft" | "publish" | "check" | "list"; draftUrl?: string }) => {
      if (kind === "check") return api.nativeMarketplaces.check(convId, marketplace, draftUrl);
      if (!revisionId) throw new Error("Generate a listing first.");
      if (kind === "list") return api.nativeMarketplaces.list(convId, marketplace, revisionId);
      // Etsy uses the shop defaults from Settings and the listing's package; Sellwild uses
      // the category and condition Anteroom worked out unless the seller changed them.
      if (kind === "draft") return api.nativeMarketplaces.saveDraft(convId, marketplace, revisionId, undefined,
        marketplace === "sellwild" && sellwildCategory && sellwildCondition ? { categoryId: sellwildCategory, conditionId: sellwildCondition } : undefined);
      const draft = drafts[marketplace];
      if (!draft || (marketplace !== "sellwild" && !draft.draft_url)) throw new Error("Save a draft first.");
      return api.nativeMarketplaces.publish(convId, marketplace, revisionId, draft.draft_url);
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

  async function listNow(marketplace: NativeMarketplace) {
    const name = marketplaceName(marketplace);
    if (await confirmDialog(`List “${title}” on ${name}?\nThis creates a live ${name} listing from the saved title, description, price, photos, category, size, condition, brand and colors.`,
      { confirmLabel: `List on ${name}` })) action.mutate({ marketplace, kind: "list" });
  }
  async function publish(marketplace: NativeMarketplace) {
    const name = marketplaceName(marketplace);
    const body = marketplace === "sellwild"
      ? "This creates a live Sellwild listing from the saved title, description, price, photos, category, condition, package size and your Sellwild ship-from address. Sellwild may hold it for review."
      : `This makes the saved ${name} draft public. Open the draft first to review its category, condition, photos, price, quantity and shipping.`;
    if (await confirmDialog(`Publish “${title}” on ${name}?\n${body}`, { confirmLabel: `Publish on ${name}` })) action.mutate({ marketplace, kind: "publish" });
  }
  async function runDelist(marketplaces: Marketplace[]) {
    const names = marketplaces.map(marketplaceName);
    const lines = marketplaces.map((marketplace) => `• ${marketplaceName(marketplace)}: ${REMOVED[marketplace].toLowerCase()}`).join("\n");
    const approved = await confirmDialog(`Delist “${title}” from ${names.length === 1 ? names[0] : `${names.length} marketplaces`}?\n${lines}\nA deleted listing can't be restored; listing again creates a new one.`,
      { variant: "destructive", confirmLabel: names.length === 1 ? `Delist from ${names[0]}` : `Delist from ${names.length}` });
    if (approved) delist.mutate(marketplaces);
  }

  const busy = disabled || action.isPending || delist.isPending || mark.isPending || native.isPending || native.isError;
  const statuses = ORDER.map((marketplace) => rowStatus(marketplace, listings[marketplace],
    NATIVE.has(marketplace) ? drafts[marketplace as NativeMarketplace] : undefined, revisionId, etsyEligible));
  const removable = ORDER.filter((marketplace) => deletable(listings[marketplace]));
  const soldSomewhere = ORDER.some((marketplace) => listings[marketplace]?.sold_here);

  const unconfirmed = (marketplace: Marketplace) => NATIVE.has(marketplace)
    && ["saving", "publishing", "unknown"].includes(drafts[marketplace as NativeMarketplace]?.state ?? "");
  function listable(marketplace: Marketplace, status: Status): boolean {
    if (!NATIVE.has(marketplace) || unconfirmed(marketplace)) return false;
    if (status.tone === "live" || status.label === "Sold here" || status.label === "In review" || listings[marketplace]?.listing_id) return false;
    if (marketplace === "etsy" && !etsyEligible) return false;
    // A sold item is not listed anywhere else.
    return !soldSomewhere;
  }
  function draftChanged(m: NativeMarketplace): boolean {
    const draft = drafts[m];
    return draft?.state === "draft" && (draft.revision_id !== revisionId || (m === "sellwild" && sellwildChoiceChanged));
  }

  function actions(marketplace: Marketplace, status: Status): ReactNode {
    const m = marketplace as NativeMarketplace;
    if (unconfirmed(marketplace)) {
      return <Button size="xs" variant="outline" disabled={action.isPending} onClick={() => action.mutate({ marketplace: m, kind: "check" })}>Check result</Button>;
    }
    if (!listable(marketplace, status)) return null;
    const draft = drafts[m];
    const pending = action.isPending && action.variables?.marketplace === m;
    if (ONE_STEP.has(marketplace)) {
      return <Button size="xs" variant="outline" disabled={busy} onClick={() => void listNow(m)}>{pending ? "Listing…" : "List…"}</Button>;
    }
    const local = m === "sellwild";
    const changed = draftChanged(m);
    const etsyBlocked = m === "etsy" && !draft?.draft_url && !etsyReady;
    const sellwildBlocked = local && (!sellwildCategory || !sellwildCondition);
    return (
      <>
        <Button size="xs" variant="outline" disabled={busy || etsyBlocked || sellwildBlocked} onClick={() => action.mutate({ marketplace: m, kind: "draft" })}>
          {pending && action.variables?.kind === "draft" ? "Saving…" : local ? "Save" : changed ? "Save changes" : "Save draft"}
        </Button>
        <Button size="xs" variant="outline" disabled={busy || draft?.state !== "draft" || changed} onClick={() => void publish(m)}>Publish…</Button>
      </>
    );
  }

  function rowMenu(marketplace: Marketplace, status: Status): ReactNode {
    const name = marketplaceName(marketplace);
    const m = marketplace as NativeMarketplace;
    const entry = listings[marketplace];
    const canList = listable(marketplace, status);
    const canDelist = deletable(entry);
    return (
      <Menu>
        <MenuTrigger render={<Button variant="ghost" size="icon-xs" aria-label={`${name} actions`} disabled={busy} />}>
          <EllipsisIcon />
        </MenuTrigger>
        <MenuPopup align="end" width="content">
          {canList && (ONE_STEP.has(marketplace)
            ? <MenuItem onClick={() => void listNow(m)}>List…</MenuItem>
            : <MenuItem disabled={drafts[m]?.state !== "draft" || draftChanged(m)} onClick={() => void publish(m)}>Publish…</MenuItem>)}
          {canDelist && <MenuItem variant="destructive" onClick={() => void runDelist([marketplace])}>Delist…</MenuItem>}
          {(canList || canDelist) && <MenuSeparator />}
          <MenuItem disabled={unconfirmed(marketplace)}
            onClick={() => setLinking({ marketplace, url: entry?.source === "seller" ? entry.url ?? "" : "" })}>Mark as listed…</MenuItem>
          {!entry?.sold_here && <MenuItem disabled={unconfirmed(marketplace)} onClick={() => mark.mutate({ marketplace, state: "sold" })}>Mark as sold</MenuItem>}
          {(entry || status.tone === "live") && (
            <MenuItem disabled={unconfirmed(marketplace)} onClick={() => mark.mutate({ marketplace, state: "not_listed" })}>Mark as not listed</MenuItem>
          )}
        </MenuPopup>
      </Menu>
    );
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
      return <p className="text-muted-foreground">Choose Etsy's default shipping and processing profiles in Settings → Connections.</p>;
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

  const summaryText = summary(statuses);
  return (
    <InspectorSection title="Marketplaces" titleId={`item-section-marketplaces-${convId}`} meta={summaryText || undefined}>
      <ul className="flex flex-col divide-y divide-border/60 text-xs" aria-label="Marketplace listings">
        {ORDER.map((marketplace, index) => {
          const status = statuses[index];
          const name = marketplaceName(marketplace);
          const extra = settings(marketplace);
          const link = linkForm(marketplace);
          const draft = NATIVE.has(marketplace) ? drafts[marketplace as NativeMarketplace] : undefined;
          return (
            <li key={marketplace} aria-label={name} className="grid grid-cols-[5rem_minmax(0,1fr)_auto] items-start gap-x-2.5 gap-y-1.5 py-1.5">
              <span className="flex min-h-6 items-center truncate font-medium text-foreground">{name}</span>
              <div className="flex min-h-6 min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
                <span role="status" className="flex min-w-0 items-center gap-1.5 text-muted-foreground [&>a]:ms-1.5">
                  {TONE_ICON[status.tone]}
                  <span className={cn(status.tone === "failed" && "text-destructive-foreground", status.tone === "attention" && "text-warning-foreground")}>{status.label}</span>
                  {status.href && <a className="inline-flex items-center gap-0.5 text-foreground/80 hover:text-foreground hover:underline" href={status.href}
                    target="_blank" rel="noopener noreferrer" aria-label={`${status.hrefLabel} on ${name}`}>{status.hrefLabel}<ExternalLinkIcon aria-hidden className="size-3" /></a>}
                </span>
                <span className="ms-auto flex shrink-0 items-center gap-1.5">{actions(marketplace, status)}</span>
              </div>
              {rowMenu(marketplace, status)}
              {(status.detail || extra || link || (draft?.state === "unknown" && !draft.draft_url && !ONE_STEP.has(marketplace) && marketplace !== "sellwild")) && (
                <div className="col-start-2 flex min-w-0 flex-col gap-1.5">
                  {link}
                  {status.detail && <p className={cn(status.tone === "failed" ? "text-destructive-foreground" : "text-muted-foreground")}
                    role={status.tone === "failed" ? "alert" : undefined}>{status.detail}</p>}
                  {extra}
                  {draft?.state === "unknown" && !draft.draft_url && !ONE_STEP.has(marketplace) && marketplace !== "sellwild" && (
                    <div className="flex items-center gap-2">
                      <Input size="compact" aria-label={`Saved ${name} draft link`} placeholder="Or paste the saved draft link"
                        value={draftLinks[marketplace as NativeMarketplace] || ""}
                        onChange={(event) => setDraftLinks({ ...draftLinks, [marketplace]: event.target.value })} />
                      <Button size="xs" variant="outline" disabled={action.isPending || !draftLinks[marketplace as NativeMarketplace]?.trim()}
                        onClick={() => action.mutate({ marketplace: marketplace as NativeMarketplace, kind: "check", draftUrl: draftLinks[marketplace as NativeMarketplace]?.trim() })}>Check link</Button>
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {removable.length > 1 && (
        <div className="flex justify-end pt-1 text-xs">
          <Button size="xs" variant="destructive-outline" disabled={busy} onClick={() => void runDelist(removable)}>
            {delist.isPending ? "Delisting…" : "Delist everywhere…"}
          </Button>
        </div>
      )}
      {(native.error || live.error || action.error || delist.error || mark.error) && (
        <p role="alert" className="text-destructive-foreground text-xs">{(mark.error || action.error || delist.error || native.error || live.error)?.message}</p>
      )}
    </InspectorSection>
  );
}
