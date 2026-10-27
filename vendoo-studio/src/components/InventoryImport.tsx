import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ImportMarketplace, MarketplaceImportPage, MarketplaceImportResult } from "../api/inventoryImport";
import { marketplaceName } from "./marketplaceNames";
import { PageError, PageNote, PageSection } from "./WorkspacePage";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

interface Importer {
  candidates: (page: number, cursor: string | null) => Promise<MarketplaceImportPage>;
  importListing: (listing: string, linkConversationId?: string) => Promise<MarketplaceImportResult>;
}

function importerFor(marketplace: ImportMarketplace): Importer {
  if (marketplace === "ebay" || marketplace === "etsy") {
    const direct = api[marketplace];
    return { candidates: (page) => direct.importCandidates(page), importListing: (listing) => direct.importListing(listing) };
  }
  return {
    candidates: (_page, cursor) => api.chromeImport.importCandidates(marketplace, cursor),
    importListing: (listing, linkConversationId) => api.chromeImport.importListing(marketplace, listing, linkConversationId),
  };
}

const SUPPORT: Record<ImportMarketplace, string> = {
  ebay: "Supports active US fixed-price listings in USD without variations.",
  etsy: "Supports active physical Etsy listings in USD without variations.",
  poshmark: "Reads your Poshmark closet through Chrome while you are signed in. Supports available listings in USD with one size.",
  mercari: "Reads your Mercari listings through Chrome while you are signed in. Supports items on sale in USD.",
  depop: "Reads your Depop shop through Chrome while you are signed in. Supports items for sale in USD with one size.",
  sellwild: "Reads your Sellwild listings through Chrome while you are signed in. Supports live listings.",
  vinted: "Reads your Vinted wardrobe through Chrome while you are signed in. Supports live listings in USD.",
};

interface ImportRequest { id: string; link?: string }

export function InventoryImport({ marketplace, accountKey, onOpenListing }: { marketplace: ImportMarketplace; accountKey: string; onOpenListing: (id: string) => void }) {
  const queryClient = useQueryClient();
  const name = marketplaceName(marketplace);
  const direct = marketplace === "ebay" || marketplace === "etsy";
  const importer = importerFor(marketplace);
  const idPattern = marketplace === "ebay" ? "[0-9]{9,15}" : marketplace === "etsy" ? "[1-9][0-9]{0,19}" : undefined;
  const [page, setPage] = useState(1);
  // Chrome marketplaces page with their own cursor; cursors[n] opens page n + 1.
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors[page - 1] ?? null;
  const [selected, setSelected] = useState<string[]>([]);
  const [itemId, setItemId] = useState("");
  const validId = idPattern ? new RegExp(`^${idPattern}$`).test(itemId.trim()) : itemId.trim().length > 0;
  const [results, setResults] = useState<Record<string, MarketplaceImportResult>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const stop = useRef(false);
  const [stopping, setStopping] = useState(false);
  const candidates = useQuery({
    queryKey: direct ? ["inventory-import", marketplace, accountKey, page] : ["inventory-import", marketplace, accountKey, page, cursor],
    queryFn: () => importer.candidates(page, cursor), retry: false,
  });
  const run = useMutation({
    mutationFn: async (requests: ImportRequest[]) => {
      stop.current = false;
      setStopping(false);
      for (const { id, link } of requests) {
        if (stop.current) break;
        setErrors((previous) => { const next = { ...previous }; delete next[id]; return next; });
        try {
          const result = link ? await importer.importListing(id, link) : await importer.importListing(id);
          setResults((previous) => ({ ...previous, [id]: result }));
          setSelected((previous) => previous.filter((value) => value !== id));
        } catch (error) {
          setErrors((previous) => ({ ...previous, [id]: error instanceof Error ? error.message : "This listing could not be imported." }));
        }
      }
    },
    retry: false,
    onSettled: () => {
      setStopping(false);
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      void queryClient.invalidateQueries({ queryKey: ["inventory-import", marketplace] });
      void queryClient.invalidateQueries({ queryKey: ["marketplace-sales"] });
    },
  });
  const goTo = (next: number) => {
    if (next > page && candidates.data?.next_cursor) {
      const known = cursors.slice(0, page);
      known[page] = candidates.data.next_cursor;
      setCursors(known);
    }
    setPage(next);
    setSelected([]);
  };
  const rows = candidates.data?.rows ?? [];
  // A row that looks like an existing item waits for the seller to link it or import it on purpose.
  const available = rows.filter((row) => !row.reason && !row.conversation_id && !row.match && !results[row.id]).map((row) => row.id);
  const imported = Object.values(results);

  return <PageSection title={`Import from ${name}`} description={`Bring your active ${name} listings and photos into Anteroom. This reads ${name} and saves local inventory; it does not change or publish listings.`}>
    <PageNote>{SUPPORT[marketplace]} Existing Anteroom items are opened again without replacing their edits or photos.</PageNote>
    <form className="ebay-search-form" onSubmit={(event) => { event.preventDefault(); if (!run.isPending) run.mutate([{ id: itemId.trim() }]); }}>
      <label className="min-w-48 flex-1 space-y-1 text-xs">{direct ? `${name} listing ID` : `${name} listing link or ID`}
        {direct ? <Input required inputMode="numeric" pattern={idPattern} maxLength={marketplace === "ebay" ? 15 : 20} value={itemId} disabled={run.isPending}
          onChange={(event) => setItemId(event.target.value)} placeholder="123456789012" />
          : <Input required maxLength={2048} value={itemId} disabled={run.isPending}
            onChange={(event) => setItemId(event.target.value)} placeholder={`Paste the ${name} listing link`} />}
      </label>
      <Button type="submit" size="sm" variant="outline" disabled={run.isPending || !validId}>Import listing</Button>
    </form>
    {candidates.error ? <PageError message={candidates.error.message} /> : null}
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span>{candidates.isPending ? "Loading active listings…" : candidates.data?.total != null ? `${candidates.data.total} active listings` : `${rows.length} listings shown`}</span>
      <Button size="xs" variant="ghost" disabled={run.isPending || candidates.isFetching} onClick={() => void candidates.refetch()}>Refresh listings</Button>
      <Button size="xs" variant="ghost" disabled={run.isPending || !available.length} onClick={() => setSelected(available)}>Select this page</Button>
      <Button size="xs" variant="outline" disabled={run.isPending || !selected.length} onClick={() => run.mutate(selected.map((id) => ({ id })))}>Import selected ({selected.length})</Button>
      {run.isPending ? <Button size="xs" variant="ghost" disabled={stopping} onClick={() => { stop.current = true; setStopping(true); }}>
        {stopping ? "Stopping after this listing…" : "Stop after this listing"}</Button> : null}
    </div>
    {rows.length ? <ul className="divide-y divide-border rounded-lg border border-border px-3">
      {rows.map((row) => {
        const localId = results[row.id]?.conversation_id || row.conversation_id;
        return <li key={row.id} className="flex items-center gap-3 py-3 text-sm">
          <input type="checkbox" aria-label={`Select ${row.title}`} checked={selected.includes(row.id)} disabled={run.isPending || Boolean(row.reason) || Boolean(localId)}
            onChange={(event) => setSelected(event.target.checked ? [...selected, row.id] : selected.filter((id) => id !== row.id))} />
          <div className="min-w-0 flex-1 break-words"><p className="font-medium">{row.title}</p>
            <p className="text-xs text-muted-foreground">{row.id}{row.price != null ? ` · $${row.price}` : ""}{row.quantity != null ? ` · ${row.quantity} available` : ""}</p>
            {row.reason ? <p className="text-xs text-muted-foreground">{row.reason}</p> : null}
            {row.match && !localId ? <p className="text-xs">Looks like “{row.match.title}” in Anteroom{row.match.price_differs && row.match.price != null ? ` (priced $${row.match.price} there)` : ""}. Link it to that item, or tick the box to import a new item.</p> : null}
          </div>
          {row.match && !localId ? <>
            <Button size="xs" variant="ghost" disabled={run.isPending} onClick={() => onOpenListing(row.match!.conversation_id)}>Compare</Button>
            <Button size="xs" variant="outline" disabled={run.isPending} onClick={() => run.mutate([{ id: row.id, link: row.match!.conversation_id }])}>Link to this item</Button>
          </> : null}
          {localId ? <Button size="xs" variant="outline" disabled={run.isPending} onClick={() => onOpenListing(localId)}>Open item</Button> : null}
        </li>;
      })}
    </ul> : candidates.isSuccess ? <PageNote>No active listings on this page.</PageNote> : null}
    <div className="flex items-center gap-2 text-xs"><span>Page {page}</span>
      <Button size="xs" variant="outline" disabled={page === 1 || run.isPending || candidates.isFetching} onClick={() => goTo(page - 1)}>Previous</Button>
      <Button size="xs" variant="outline" disabled={!candidates.data?.has_more || run.isPending || candidates.isFetching} onClick={() => goTo(page + 1)}>Next</Button>
    </div>
    {run.isPending ? <p role="status" className="text-sm">Importing listings and photos…</p> : null}
    {imported.length ? <div className="space-y-2" role="status">
      {imported.map((result) => <div key={result.listing_id} className="rounded-lg border border-border p-3 text-xs">
        <p>{result.listing_id}: {result.linked ? "Linked" : result.reused ? "Already in Anteroom" : "Imported"} · {result.photo_count} photos</p>
        {result.warnings.map((warning) => <p key={warning} className="text-muted-foreground">{warning}</p>)}
        <Button size="xs" variant="ghost" disabled={run.isPending} onClick={() => onOpenListing(result.conversation_id)}>Open item</Button>
      </div>)}
    </div> : null}
    {Object.entries(errors).map(([id, message]) => <PageError key={id} message={`${id}: ${message}`} />)}
  </PageSection>;
}
