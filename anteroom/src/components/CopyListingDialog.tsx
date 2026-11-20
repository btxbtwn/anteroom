import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { CopyPreview } from "../api/listingCopy";
import { PriceDialog } from "./PriceDropParts";
import { PageError, PageNote } from "./WorkspacePage";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";

export function CopyListingDialog({ conversationId, onClose, onCopied }: { conversationId: string; onClose: () => void; onCopied: (id: string) => void }) {
  const preview = useQuery({ queryKey: ["listing-copy-preview", conversationId], queryFn: () => api.conversations.copyPreview(conversationId),
    retry: false, staleTime: Infinity, gcTime: 0, refetchOnWindowFocus: false });
  return preview.data ? <CopyListingForm preview={preview.data} onReload={() => preview.refetch()} reloading={preview.isFetching} reloadError={preview.error?.message} onClose={onClose} onCopied={onCopied} />
    : <PriceDialog title="Copy listing" description="Create a new draft from this item’s saved details." busy={false} onClose={onClose}
      footer={<><Button variant="outline" onClick={onClose}>Cancel</Button>{preview.error ? <Button onClick={() => void preview.refetch()}>Reload source</Button> : null}</>}>
      {preview.error ? <PageError message={preview.error.message} /> : <PageNote>Loading saved listing and photos…</PageNote>}
    </PriceDialog>;
}

function CopyListingForm({ preview, onReload, reloading, reloadError, onClose, onCopied }: { preview: CopyPreview; onReload: () => Promise<unknown>; reloading: boolean; reloadError?: string; onClose: () => void; onCopied: (id: string) => void }) {
  const client = useQueryClient();
  const [requestId] = useState(() => crypto.randomUUID().replace(/-/g, ""));
  const [title, setTitle] = useState(preview.title);
  const [sku, setSku] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [includePhotos, setIncludePhotos] = useState(true);
  const [includeCost, setIncludeCost] = useState(false);
  const save = useMutation({ mutationFn: () => api.conversations.copy(preview.source_id, {
    request_id: requestId, expected_basis: preview.expected_basis, title: title.trim(), sku: sku.trim(), quantity: Number(quantity), include_photos: includePhotos, include_cost: includeCost,
  }), retry: false, onSuccess: (conversation) => {
    client.setQueryData(["conversation", conversation.id], conversation);
    void client.invalidateQueries({ queryKey: ["conversations"] });
    void client.invalidateQueries({ queryKey: ["analytics"] });
    onCopied(conversation.id);
  } });
  const check = useMutation({ mutationFn: () => api.conversations.copyResult(preview.source_id, requestId), retry: false,
    onSuccess: (conversation) => { void client.invalidateQueries({ queryKey: ["conversations"] }); onCopied(conversation.id); } });
  const busy = reloading || save.isPending || check.isPending;
  const valid = Boolean(title.trim() && /^\d+$/.test(quantity) && Number(quantity) >= 1 && Number(quantity) <= 1000000);
  return <PriceDialog title="Copy listing" description={`Use ${preview.source_title} as the starting point for a separate item. Review its description, condition and measurements before listing.`} busy={busy} onClose={onClose}
    footer={<>{save.error ? <Button variant="ghost" disabled={busy} onClick={() => check.mutate()}>Check copy result</Button> : null}<Button variant="ghost" disabled={busy} onClick={() => void onReload()}>Reload source</Button><Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button><Button disabled={busy || !valid} onClick={() => save.mutate()}>{save.isPending ? "Copying…" : "Create local draft"}</Button></>}>
    <PageNote>The copy starts as a draft with its own quantity and SKU. Marketplace links, sales history, chats and sourcing-box assignment stay with the original. Creating a copy sends nothing to a marketplace.</PageNote>
    {!preview.has_listing ? <PageNote>This item has no saved listing fields yet. Its saved Item Details and selected photos can still be copied.</PageNote> : null}
    <label className="block space-y-1">Inventory name<Input aria-label="Inventory name" required maxLength={200} value={title} disabled={busy} onChange={(event) => setTitle(event.target.value)} /></label>
    <label className="block space-y-1">New SKU (optional)<Input aria-label="New SKU" maxLength={120} value={sku} disabled={busy} onChange={(event) => setSku(event.target.value)} /></label>
    <label className="block space-y-1">Quantity for the new item<Input aria-label="Quantity for the new item" inputMode="numeric" value={quantity} disabled={busy} onChange={(event) => setQuantity(event.target.value)} /></label>
    <label className="flex items-center gap-2"><Checkbox checked={includePhotos} disabled={busy || preview.photo_count === 0} onCheckedChange={(checked) => setIncludePhotos(checked === true)} />Copy {preview.photo_count} saved photos</label>
    {!preview.photo_count ? <PageNote>No local photos are saved. Add photos to the new draft after copying.</PageNote> : null}
    <label className="flex items-center gap-2"><Checkbox checked={includeCost} disabled={busy} onCheckedChange={(checked) => setIncludeCost(checked === true)} />Copy recorded cost</label>
    {save.error || reloadError || check.error ? <PageError message={check.error?.message || save.error?.message || reloadError || "Copy failed"} /> : null}
  </PriceDialog>;
}
