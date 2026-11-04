import type { ComponentProps } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { MarketplacesLayout } from "./MarketplacesSection";
import { marketplaceName } from "./marketplaceNames";
import { Button } from "./ui/button";

export function useListingMode() {
  return useQuery({ queryKey: ["settings-ui"], queryFn: api.settings.ui });
}

export function VendooLayout({ convId, revisionId, disabled, ids, selected, onSelect, railActions,
  railStatus, children }: ComponentProps<typeof MarketplacesLayout>) {
  const client = useQueryClient();
  const key = ["vendoo-draft", convId];
  const draft = useQuery({ queryKey: key,
    queryFn: () => api.vendooDraft.status(convId),
    refetchInterval: query => query.state.data?.status === "dispatched" ? 1500 : false });
  const send = useMutation({ retry: false,
    mutationFn: () => api.vendooDraft.send(convId, revisionId!),
    onSuccess: result => { client.setQueryData(key, result); void client.invalidateQueries({ queryKey: ["jobs", convId] }); },
  });
  const running = draft.data?.status === "dispatched" || send.isPending;
  const check = useMutation({ retry: false, mutationFn: () => api.vendooDraft.check(convId),
    onSuccess: result => client.setQueryData(key, result) });
  return <div className="flex min-h-0 flex-1">
    <nav aria-label="Listing forms" className="flex w-40 shrink-0 flex-col gap-1 border-r p-2">
      {ids.map(id => <Button key={id} size="xs" variant={id === selected ? "secondary" : "ghost"}
        aria-current={id === selected ? "page" : undefined} onClick={() => onSelect(id)}>
        {id === "general" ? "General" : marketplaceName(id)}
      </Button>)}
      <div className="mt-auto flex flex-col gap-1 border-t pt-2">{railActions}{railStatus}</div>
    </nav>
    <div className="flex min-w-0 flex-1 flex-col gap-3 overflow-y-auto p-3">
      <section aria-label="Vendoo draft" className="flex flex-wrap items-center gap-2 rounded-lg bg-muted/40 p-2 text-xs">
        <span>Vendoo mode</span>
        <Button size="xs" disabled={disabled || running || !revisionId} onClick={() => send.mutate()}>
          {running ? "Sending to Vendoo…" : draft.data?.url ? "Save changes to Vendoo" : "Send to Vendoo"}
        </Button>
        {draft.data?.url && <Button size="xs" variant="outline"
          render={<a href={draft.data.url} target="_blank" rel="noopener noreferrer" />}>Review in Vendoo</Button>}
        {draft.data?.url && <Button size="xs" variant="ghost" disabled={running || check.isPending}
          onClick={() => check.mutate()}>Check saved draft</Button>}
        <p className="w-full text-muted-foreground">Save a private draft, then review and list from Vendoo. Direct marketplace actions are off.</p>
        {draft.data?.status === "completed" && <p role="status">Saved in Vendoo.</p>}
        {(send.error || check.error || draft.data?.error) && <p role="alert" className="text-destructive-foreground">{send.error?.message || check.error?.message || draft.data?.error}</p>}
      </section>
      {children}
    </div>
  </div>;
}
