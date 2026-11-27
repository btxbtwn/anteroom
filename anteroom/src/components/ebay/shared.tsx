import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckIcon, ChevronLeftIcon, ChevronRightIcon, InboxIcon, RefreshCwIcon, UploadCloudIcon } from "lucide-react";
import { api } from "../../api/client";
import type { EbayResult, EbayRow } from "../../api/ebay";
import { PageError } from "../WorkspacePage";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Input } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { Spinner } from "../ui/spinner";
import { ActionForm } from "./ActionForm";

/** A metric's figure. A missing figure keeps its words but drops to body size and muted color. */
export function MetricValue({ value }: { value: string }) {
  return <dd data-empty={value === "Not available" || value === "—" || undefined}>{value}</dd>;
}

export function RowCards({ rows, onSelect, selected }: { rows: EbayRow[]; onSelect?: (row: EbayRow) => void; selected?: string }) {
  return rows.length ? <div className="ebay-records">{rows.map((row) => <article key={row.id} className="ebay-record" data-selected={selected === row.id}>
    <div className="ebay-record-header">
      <div className="ebay-record-heading"><h3>{row.title || row.id}</h3><p className="ebay-record-id">ID {row.id}</p></div>
      {row.status ? <span className="ebay-status">{row.status.replace(/_/g, " ").toLowerCase()}</span> : null}
      {row.amount != null ? <span className="ebay-record-amount">${row.amount}</span> : null}
    </div>
    {row.subtitle ? <p className="ebay-record-description">{row.subtitle}</p> : null}
    {Object.keys(row.fields).length ? <dl className="ebay-record-fields">{Object.entries(row.fields).map(([key, value]) => key === "url"
      ? typeof value === "string" && /^https:\/\/(www\.)?ebay\.com\//i.test(value) ? <div key={key}><dt>Listing</dt><dd><a href={value} target="_blank" rel="noreferrer" className="underline">View on eBay</a></dd></div> : null
      : <div key={key}><dt>{key.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/Id$/, "ID")}</dt><dd>{value ?? "Not available"}</dd></div>)}</dl> : null}
    {row.children?.length ? <ul className="mt-4 space-y-3 border-t border-border pt-4 text-sm">{row.children.map((child) => <li key={child.id} className="break-words">
      <span className="font-medium">{child.title}</span><span className="block text-xs text-muted-foreground mt-1">{Object.entries(child.fields).map(([key, value]) => `${key}: ${value ?? "—"}`).join(" · ")}</span>
    </li>)}</ul> : null}
    {onSelect ? <div className="ebay-record-actions"><Button size="sm" variant="outline" aria-label={`Select ${row.title || row.id}`} onClick={() => onSelect(row)}>
      {selected === row.id ? <CheckIcon /> : null}{selected === row.id ? "Selected" : "Select"}
    </Button></div> : null}
  </article>)}</div> : <div className="ebay-empty"><InboxIcon aria-hidden="true" /><p>No items to show here yet.</p></div>;
}

const SECTION_LABELS = {
  "inventory": "listings",
  "offers": "listing offers",
  "research": "research results",
  "finances": "earnings",
  "payouts": "payouts",
  "traffic": "listing performance",
  "inbox": "inbox",
  "messages": "messages",
  "feedback": "feedback",
  "orders": "orders",
  "order": "order details",
  "disputes": "payment disputes",
  "dispute": "dispute details",
  "activity": "activity",
  "promotions": "promotions",
  "promotion-reports": "promotion results",
  "campaigns": "ad campaigns",
  "ad-reports": "advertising reports",
  "ad-report": "report details",
  "emails": "store emails",
} as const;

export function EbayRowsLoading({ label, compact = false }: { label: string; compact?: boolean }) {
  return <div className={compact ? "space-y-2" : "ebay-records"} role="status">
    <span className="sr-only">{label}</span>
    {[0, 1, 2].map((row) => <div key={row} aria-hidden="true"
      className={compact ? "flex items-center gap-2 p-2" : "ebay-record space-y-2"}>
      {compact ? <Skeleton className="size-4" /> : null}
      <Skeleton className="h-4 w-40 max-w-full" />
      {!compact ? <Skeleton className="h-3 w-24" /> : null}
    </div>)}
  </div>;
}

export function RemoteList({ section, params = {}, onSelect, selected }: { section: keyof typeof SECTION_LABELS; params?: Record<string, string | number>; onSelect?: (row: EbayRow) => void; selected?: string }) {
  const [offset, setOffset] = useState(0);
  const result = useQuery({ queryKey: ["ebay", section, params, offset], queryFn: () => api.ebay.read(section, { ...params, offset }), retry: false, staleTime: 30000 });
  const count = result.data?.total;
  const label = SECTION_LABELS[section];
  const size = section === "finances" ? 10 : 25;
  return <div className="min-w-0">
    <div className="ebay-list-toolbar"><span>{count != null ? `${count} ${count === 1 ? "result" : "results"}` : result.isPending ? null : `${result.data?.rows.length || 0} shown`}</span>
      <Button size="sm" variant="ghost" disabled={result.isFetching} onClick={() => void result.refetch()} aria-label={`Refresh ${label}`}>
        {result.isFetching ? <Spinner /> : <RefreshCwIcon />}Refresh
      </Button></div>
    {result.error ? <PageError message={result.error.message} /> : null}
    {result.isPending ? <EbayRowsLoading label={`Loading ${label}`} /> : null}
    {result.data ? <RowCards rows={result.data.rows} onSelect={onSelect} selected={selected} /> : null}
    {offset > 0 || result.data?.nextOffset != null ? <div className="ebay-pagination">
      <span>Page {Math.floor(offset / size) + 1}</span><div>
        <Button size="sm" variant="outline" disabled={offset === 0 || result.isFetching} onClick={() => setOffset(Math.max(0, offset - size))}><ChevronLeftIcon />Previous</Button>
        <Button size="sm" variant="outline" disabled={result.data?.nextOffset == null || result.isFetching} onClick={() => setOffset(result.data!.nextOffset!)}>Next<ChevronRightIcon /></Button>
      </div></div> : null}
  </div>;
}

export function ListingPicker({ selected, onChange, advertisingAccount }: {
  selected: string[]; onChange: (ids: string[]) => void; advertisingAccount?: string;
}) {
  const advertising = advertisingAccount !== undefined;
  const [search, setSearch] = useState("");
  const listings = useQuery({ queryKey: ["ebay", "local-listings"], queryFn: api.ebayOffers.status, enabled: !advertising });
  const active = useQuery({ queryKey: ["ebay", "advertising-listings", advertisingAccount],
    queryFn: () => api.ebay.read("advertising-listings"), enabled: advertising, retry: false });
  const query = advertising ? active : listings;
  const items = advertising ? (active.isError ? undefined : active.data?.rows) : listings.data?.listings.map(({ listingId, title }) => ({ id: listingId, title }));
  const visible = items?.filter((item) => item.title.toLowerCase().includes(search.trim().toLowerCase())) ?? [];
  const selectedIds = new Set(selected);
  return <fieldset className="ebay-selection"><legend>{advertising ? "Active eBay listings" : "Select listings"}</legend>
    <p className="ebay-selection-meta" aria-live="polite">{selected.length} selected{advertising ? "" : " · up to 50 listings per discount"}</p>
    {query.error ? <PageError message={query.error.message} /> : null}
    {advertising && query.isPending ? <EbayRowsLoading label="Loading active eBay listings" compact /> : null}
    <Input nativeInput aria-label="Filter listings" type="search" placeholder="Find a listing…" value={search} onChange={(event) => setSearch(event.target.value)} />
    {advertising ? <div className="flex flex-wrap gap-2">
      <Button type="button" variant="outline" disabled={!items?.length || active.isFetching}
        onClick={() => onChange(items?.map((item) => item.id) ?? [])}>Select all active listings</Button>
      <Button type="button" variant="outline" disabled={!selected.length} onClick={() => onChange([])}>Clear selection</Button>
      <Button type="button" variant="ghost" disabled={active.isFetching} onClick={() => void active.refetch()}>Refresh</Button>
    </div> : null}
    <div className="ebay-selection-list">
      {visible.map((item) => <label key={item.id}>
        <Checkbox className="mt-0.5" checked={selectedIds.has(item.id)} onCheckedChange={(next) => onChange(next === true ? [...selected, item.id] : selected.filter((id) => id !== item.id))} />{item.title}
      </label>)}
      {items && !visible.length ? <p className="text-sm text-muted-foreground py-4">
        {search ? "No listings match your search." : advertising ? "No active US fixed-price listings found." : "Import listings from eBay to select them here."}
      </p> : null}
    </div>
  </fieldset>;
}

export function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.onerror = () => reject(new Error("Could not read the image.")); reader.readAsDataURL(file); });
}

export function EvidenceUploader({ disputeId, onResult }: { disputeId: string; onResult?: (result: EbayResult) => void }) {
  const [image, setImage] = useState<{ data: string; mime: string } | null>(null);
  const [error, setError] = useState("");
  return <div className="space-y-4">
    <div className="ebay-upload"><label><span className="flex items-center gap-2 font-medium"><UploadCloudIcon className="size-4" aria-hidden="true" />Upload evidence</span>
      <span className="text-xs text-muted-foreground">JPEG or PNG · up to 1.5 MB. Choose a file, then review the upload.</span>
      <input type="file" aria-label="Evidence image" accept="image/jpeg,image/png" onChange={async (event) => {
        const file = event.target.files?.[0]; setImage(null); setError("");
        if (!file) return;
        if (file.size > 1500000) { setError("The image is too large."); return; }
        try { setImage({ data: await fileBase64(file), mime: file.type }); } catch { setError("Could not read the image."); }
      }} />
    </label></div>
    {error ? <PageError message={error} /> : null}
    {image ? <ActionForm key={image.data.slice(-32)} title="Upload evidence image"
      kind="upload_evidence" context={{ ...image, dispute_id: disputeId }} onResult={onResult} /> : null}
  </div>;
}
