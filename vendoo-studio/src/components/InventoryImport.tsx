import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ImportMarketplace, MarketplaceImportResult } from "../api/inventoryImport";
import { marketplaceName } from "./marketplaceNames";
import { PageError, PageNote, PageSection } from "./WorkspacePage";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

export function InventoryImport({ marketplace, accountKey, onOpenListing }: { marketplace: ImportMarketplace; accountKey: string; onOpenListing: (id: string) => void }) {
  const queryClient = useQueryClient();
  const name = marketplaceName(marketplace);
  const importer = api[marketplace];
  const idPattern = marketplace === "ebay" ? "[0-9]{9,15}" : "[1-9][0-9]{0,19}";
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  const [itemId, setItemId] = useState("");
  const validId = new RegExp(`^${idPattern}$`).test(itemId.trim());
  const [results, setResults] = useState<Record<string, MarketplaceImportResult>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const stop = useRef(false);
  const [stopping, setStopping] = useState(false);
  const candidates = useQuery({ queryKey: ["inventory-import", marketplace, accountKey, page], queryFn: () => importer.importCandidates(page), retry: false });
  const run = useMutation({
    mutationFn: async (ids: string[]) => {
      stop.current = false;
      setStopping(false);
      for (const id of ids) {
        if (stop.current) break;
        setErrors((previous) => { const next = { ...previous }; delete next[id]; return next; });
        try {
          const result = await importer.importListing(id);
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
  const rows = candidates.data?.rows ?? [];
  const available = rows.filter((row) => !row.reason && !row.conversation_id && !results[row.id]).map((row) => row.id);
  const imported = Object.values(results);

  return <PageSection title={`Import from ${name}`} description={`Bring your active ${name} listings and photos into Anteroom. This reads ${name} and saves local inventory; it does not change or publish listings.`}>
    <PageNote>{marketplace === "ebay" ? "Supports active US fixed-price listings in USD without variations." : "Supports active physical Etsy listings in USD without variations."} Existing Anteroom items are opened again without replacing their edits or photos.</PageNote>
    <form className="ebay-search-form" onSubmit={(event) => { event.preventDefault(); if (!run.isPending) run.mutate([itemId.trim()]); }}>
      <label className="min-w-48 flex-1 space-y-1 text-xs">{name} listing ID
        <Input required inputMode="numeric" pattern={idPattern} maxLength={marketplace === "ebay" ? 15 : 20} value={itemId} disabled={run.isPending}
          onChange={(event) => setItemId(event.target.value)} placeholder="123456789012" />
      </label>
      <Button type="submit" size="sm" variant="outline" disabled={run.isPending || !validId}>Import listing</Button>
    </form>
    {candidates.error ? <PageError message={candidates.error.message} /> : null}
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span>{candidates.isPending ? "Loading active listings…" : candidates.data?.total != null ? `${candidates.data.total} active listings` : `${rows.length} listings shown`}</span>
      <Button size="xs" variant="ghost" disabled={run.isPending || candidates.isFetching} onClick={() => void candidates.refetch()}>Refresh listings</Button>
      <Button size="xs" variant="ghost" disabled={run.isPending || !available.length} onClick={() => setSelected(available)}>Select this page</Button>
      <Button size="xs" variant="outline" disabled={run.isPending || !selected.length} onClick={() => run.mutate([...selected])}>Import selected ({selected.length})</Button>
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
          </div>
          {localId ? <Button size="xs" variant="outline" disabled={run.isPending} onClick={() => onOpenListing(localId)}>Open item</Button> : null}
        </li>;
      })}
    </ul> : candidates.isSuccess ? <PageNote>No active listings on this page.</PageNote> : null}
    <div className="flex items-center gap-2 text-xs"><span>Page {page}</span>
      <Button size="xs" variant="outline" disabled={page === 1 || run.isPending || candidates.isFetching} onClick={() => { setPage(page - 1); setSelected([]); }}>Previous</Button>
      <Button size="xs" variant="outline" disabled={!candidates.data?.has_more || run.isPending || candidates.isFetching} onClick={() => { setPage(page + 1); setSelected([]); }}>Next</Button>
    </div>
    {run.isPending ? <p role="status" className="text-sm">Importing listings and photos…</p> : null}
    {imported.length ? <div className="space-y-2" role="status">
      {imported.map((result) => <div key={result.listing_id} className="rounded-lg border border-border p-3 text-xs">
        <p>{result.listing_id}: {result.reused ? "Already in Anteroom" : "Imported"} · {result.photo_count} photos</p>
        {result.warnings.map((warning) => <p key={warning} className="text-muted-foreground">{warning}</p>)}
        <Button size="xs" variant="ghost" disabled={run.isPending} onClick={() => onOpenListing(result.conversation_id)}>Open item</Button>
      </div>)}
    </div> : null}
    {Object.entries(errors).map(([id, message]) => <PageError key={id} message={`${id}: ${message}`} />)}
  </PageSection>;
}
