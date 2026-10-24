import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckIcon, ChevronLeftIcon, ChevronRightIcon, InboxIcon, RefreshCwIcon, UploadCloudIcon } from "lucide-react";
import { api } from "../../api/client";
import type { EbayResult, EbayRow } from "../../api/ebay";
import { PageError } from "../WorkspacePage";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Spinner } from "../ui/spinner";
import { ActionForm } from "./ActionForm";

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

export function RemoteList({ section, params = {}, onSelect, selected }: { section: string; params?: Record<string, string | number>; onSelect?: (row: EbayRow) => void; selected?: string }) {
  const [offset, setOffset] = useState(0);
  const result = useQuery({ queryKey: ["ebay", section, params, offset], queryFn: () => api.ebay.read(section, { ...params, offset }), retry: false, staleTime: 30000 });
  const count = result.data?.total;
  const size = section === "finances" ? 10 : 25;
  return <div className="min-w-0">
    <div className="ebay-list-toolbar"><span>{count != null ? `${count} ${count === 1 ? "result" : "results"}` : result.isPending ? "Loading…" : `${result.data?.rows.length || 0} shown`}</span>
      <Button size="sm" variant="ghost" disabled={result.isFetching} onClick={() => void result.refetch()} aria-label={`Refresh ${section}`}>
        {result.isFetching ? <Spinner /> : <RefreshCwIcon />}Refresh
      </Button></div>
    {result.error ? <PageError message={result.error.message} /> : null}
    {result.isPending ? <div className="ebay-empty" role="status"><Spinner /><p>Loading your eBay data…</p></div> : null}
    {result.data ? <RowCards rows={result.data.rows} onSelect={onSelect} selected={selected} /> : null}
    {offset > 0 || result.data?.nextOffset != null ? <div className="ebay-pagination">
      <span>Page {Math.floor(offset / size) + 1}</span><div>
        <Button size="sm" variant="outline" disabled={offset === 0 || result.isFetching} onClick={() => setOffset(Math.max(0, offset - size))}><ChevronLeftIcon />Previous</Button>
        <Button size="sm" variant="outline" disabled={result.data?.nextOffset == null || result.isFetching} onClick={() => setOffset(result.data!.nextOffset!)}>Next<ChevronRightIcon /></Button>
      </div></div> : null}
  </div>;
}

export function ListingPicker({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const [search, setSearch] = useState("");
  const listings = useQuery({ queryKey: ["ebay", "local-listings"], queryFn: api.ebayOffers.status });
  const visible = listings.data?.listings.filter((item) => item.title.toLowerCase().includes(search.toLowerCase())) || [];
  return <fieldset className="ebay-selection"><legend>Select listings</legend>
    <p className="ebay-selection-meta">{selected.length} selected · up to 50 listings per discount</p>
    {listings.error ? <PageError message={listings.error.message} /> : null}
    <Input nativeInput aria-label="Filter listings" type="search" placeholder="Find a listing…" value={search} onChange={(event) => setSearch(event.target.value)} />
    <div className="ebay-selection-list">
      {visible.map((item) => <label key={item.listingId}>
        <input type="checkbox" checked={selected.includes(item.listingId)} onChange={(event) => onChange(event.target.checked ? [...selected, item.listingId] : selected.filter((id) => id !== item.listingId))} />{item.title}
      </label>)}
      {listings.data && !visible.length ? <p className="text-sm text-muted-foreground py-4">{search ? "No listings match your search." : "Import listings from Vendoo to select them here."}</p> : null}
    </div>
  </fieldset>;
}

export function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.onerror = () => reject(new Error("Could not read the image.")); reader.readAsDataURL(file); });
}

export function ImageUploader({ disputeId, onResult }: { disputeId?: string; onResult?: (result: EbayResult) => void }) {
  const [image, setImage] = useState<{ data: string; mime: string } | null>(null);
  const [error, setError] = useState("");
  return <div className="space-y-4">
    <div className="ebay-upload"><label><span className="flex items-center gap-2 font-medium"><UploadCloudIcon className="size-4" aria-hidden="true" />{disputeId ? "Upload evidence" : "Add a listing photo"}</span>
      <span className="text-xs text-muted-foreground">JPEG or PNG · up to {disputeId ? "1.5" : "8"} MB. Choose a file, then review the upload.</span>
      <input type="file" aria-label={disputeId ? "Evidence image" : "Listing image"} accept="image/jpeg,image/png" onChange={async (event) => {
        const file = event.target.files?.[0]; setImage(null); setError("");
        if (!file) return;
        if (file.size > (disputeId ? 1500000 : 8000000)) { setError("The image is too large."); return; }
        try { setImage({ data: await fileBase64(file), mime: file.type }); } catch { setError("Could not read the image."); }
      }} />
    </label></div>
    {error ? <PageError message={error} /> : null}
    {image ? <ActionForm key={image.data.slice(-32)} title={disputeId ? "Upload evidence image" : "Upload listing image"}
      kind={disputeId ? "upload_evidence" : "upload_image"} context={{ ...image, dispute_id: disputeId || "" }} onResult={onResult} /> : null}
  </div>;
}
