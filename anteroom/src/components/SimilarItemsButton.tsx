import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SimilarItem, SimilarPair, SimilarReason } from "../api/types";
import { confirmDialog } from "../ui/confirmDialog";
import { formatMoney } from "./analyticsFormat";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogPanel,
  DialogPopup,
  DialogTitle,
  DialogTrigger,
} from "./ui/dialog";
import { Spinner } from "./ui/spinner";

const REASONS: Record<SimilarReason, string> = {
  same_photo: "Same photo",
  same_sku: "Same SKU",
  similar_title: "Similar title",
  similar_photo: "Cover photos look alike",
};

function itemMeta(item: SimilarItem): string {
  return [
    item.status[0]?.toUpperCase() + item.status.slice(1),
    item.brand,
    item.size ? `Size ${item.size}` : null,
    item.price != null ? formatMoney(item.price) : null,
    item.sku ? `SKU ${item.sku}` : null,
  ].filter(Boolean).join(" · ");
}

/** Likely duplicate items for the seller to review. Nothing changes until they merge or dismiss a pair. */
export function SimilarItemsButton({ onSelect }: { onSelect: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const { data, isFetching, error } = useQuery({
    queryKey: ["similar-items"],
    queryFn: api.similarItems.list,
    enabled: open,
    staleTime: 0,
  });
  const refresh = () => {
    for (const key of ["similar-items", "conversations", "analytics"]) void queryClient.invalidateQueries({ queryKey: [key] });
  };
  const dismiss = useMutation({
    mutationFn: (pair: SimilarPair) => api.similarItems.dismiss(pair.first.id, pair.second.id),
    onSuccess: refresh,
  });
  const merge = useMutation({
    mutationFn: ({ keep, other }: { keep: SimilarItem; other: SimilarItem }) => api.similarItems.merge(keep.id, other.id),
    onSuccess: refresh,
  });
  const busy = dismiss.isPending || merge.isPending;
  const failure = dismiss.error ?? merge.error;

  const keep = async (kept: SimilarItem, other: SimilarItem) => {
    const ok = await confirmDialog(
      `Keep “${kept.title}” and merge “${other.title}” into it?\n`
        + `Its marketplace links, sales and labels move to the item you keep, and it moves to Finished. Nothing is deleted or delisted.`,
      { confirmLabel: "Merge" },
    );
    if (ok) merge.mutate({ keep: kept, other });
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button size="xs" variant="outline" aria-label="Find similar items" />}>
        Find similar items
      </DialogTrigger>
      <DialogPopup className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Similar items</DialogTitle>
          <DialogDescription>
            Items that share a photo or SKU, or whose titles match with the same brand and size. Open them to compare, keep one and
            merge the other into it, or mark them as different.
          </DialogDescription>
        </DialogHeader>
        <DialogPanel aria-live="polite" className="space-y-3">
          {failure ? <p role="alert" className="text-sm text-destructive-foreground">{failure.message}</p> : null}
          {isFetching && !data ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner size="sm" tone="muted" />
              Comparing titles, SKUs and photos…
            </p>
          ) : error ? (
            <p role="alert" className="text-sm text-destructive-foreground">{error.message}</p>
          ) : !data?.pairs.length ? (
            <p className="text-sm text-muted-foreground">No similar items found.</p>
          ) : (
            <>
              {data.total > data.pairs.length ? (
                <p className="text-xs text-muted-foreground">Showing the {data.pairs.length} closest of {data.total} pairs.</p>
              ) : null}
              <SimilarPairList
                pairs={data.pairs}
                busy={busy}
                onOpen={(id) => { setOpen(false); onSelect(id); }}
                onKeep={(kept, other) => void keep(kept, other)}
                onDismiss={(pair) => dismiss.mutate(pair)}
              />
            </>
          )}
        </DialogPanel>
        <DialogFooter>
          <DialogClose render={<Button />}>Done</DialogClose>
        </DialogFooter>
      </DialogPopup>
    </Dialog>
  );
}

export function SimilarPairList({ pairs, busy, onOpen, onKeep, onDismiss }: {
  pairs: SimilarPair[];
  busy: boolean;
  onOpen: (id: string) => void;
  onKeep: (kept: SimilarItem, other: SimilarItem) => void;
  onDismiss: (pair: SimilarPair) => void;
}) {
  return pairs.map((pair) => (
    <section key={`${pair.first.id}-${pair.second.id}`} aria-label={`${pair.first.title} and ${pair.second.title}`}
      className="space-y-2 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {pair.reasons.map((reason) => <Badge key={reason} variant="secondary">{REASONS[reason]}</Badge>)}
        <Button size="xs" variant="ghost" className="ms-auto" disabled={busy} onClick={() => onDismiss(pair)}>
          Not duplicates
        </Button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {[[pair.first, pair.second], [pair.second, pair.first]].map(([item, other]) => (
          <div key={item.id} className="flex min-w-0 gap-2.5">
            {item.cover_url ? (
              <img src={item.cover_url} alt="" className="size-14 shrink-0 rounded-md object-cover" />
            ) : <div aria-hidden className="size-14 shrink-0 rounded-md bg-muted" />}
            <div className="min-w-0 flex-1 space-y-1">
              <button type="button" className="block w-full truncate text-start text-sm font-medium text-foreground hover:underline"
                onClick={() => onOpen(item.id)}>
                {item.title}
              </button>
              <p className="truncate text-xs text-muted-foreground">{itemMeta(item)}</p>
              <Button size="xs" variant="outline" disabled={busy} onClick={() => onKeep(item, other)}>
                Keep this one
              </Button>
            </div>
          </div>
        ))}
      </div>
    </section>
  ));
}
