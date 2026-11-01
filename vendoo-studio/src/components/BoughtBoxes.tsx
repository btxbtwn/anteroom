import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SourceBox, SourceBoxInput, SourceBoxes, SourceStoreResults } from "../api/types";
import { confirmDialog } from "../ui/confirmDialog";
import { toastManager } from "./ui/toast";
import { formatDays, formatMoney } from "./analyticsFormat";
import {
  BOXES_QUERY_KEY,
  boxProgress,
  boxScorecard,
  boxStoreKey,
  boxStoreName,
  formatPercent,
  profitPerHundred,
} from "./boxPurchases";
import { DraftInput } from "./DraftInput";
import { Button, InlineButton } from "./ui/button";
import { Input } from "./ui/input";
import { EmptyRow, FormError, FormField, PageSection, Panel } from "./WorkspacePage";
import { Profit, SourcingItem, SourcingList, SourcingSetting, sourcingReasonClassName, sourcingTitleClassName } from "./sourcingUi";

/**
 * The boxes the seller bought and what each has made back so far: money in
 * against what its sales brought in after fees and shipping. Listings join a
 * box when its photos are bulk uploaded, or from Item Details.
 */
export function BoughtBoxes() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: BOXES_QUERY_KEY, queryFn: api.boxes.list });
  const onSaved = (data: SourceBoxes) => queryClient.setQueryData(BOXES_QUERY_KEY, data);
  const onError = (error: Error) =>
    toastManager.add({ type: "error", title: "Could not save the box", description: error.message });
  const update = useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: Partial<SourceBoxInput> }) => api.boxes.update(id, changes),
    onSuccess: onSaved,
    onError,
  });
  const remove = useMutation({ mutationFn: api.boxes.remove, onSuccess: onSaved, onError });
  const create = useMutation({ mutationFn: api.boxes.create, onSuccess: onSaved, onError });
  const [adding, setAdding] = useState(false);
  const data = query.data;

  const onRemove = async (box: SourceBox) => {
    const ok = await confirmDialog(
      `Remove “${box.title}” from your boxes? Its listings stay; they just won't belong to a box.`,
      { variant: "destructive", confirmLabel: "Remove box" },
    );
    if (ok) remove.mutate(box.id);
  };

  return (
    <PageSection
      aria-label="Boxes you bought"
      title="Boxes you bought"
      actions={!adding ? <Button variant="outline" size="xs" onClick={() => setAdding(true)}>Add a box</Button> : null}
      description={<>
        What each box cost and what its sales have earned after fees. Pick the box when you upload its photos,
        and record pieces you didn’t list so their cost counts toward the ones that sell.
      </>}
    >
      {adding ? (
        <AddBoxForm
          saving={create.isPending}
          onCancel={() => setAdding(false)}
          onSave={(box) => create.mutate(box, { onSuccess: () => setAdding(false) })}
        />
      ) : null}
      {query.isError ? <FormError>{(query.error as Error).message}</FormError> : null}
      {data && data.boxes.length === 0 && !adding ? (
        <EmptyRow>
          No boxes yet. Press <strong className="font-medium text-foreground">I bought this</strong> on a box above once you've paid for it.
        </EmptyRow>
      ) : null}
      {data && data.stores.length > 1 ? (
        <div className="grid gap-3 @lg/page:grid-cols-2 @3xl/page:grid-cols-3">
          {data.stores.map((store) => <StoreCard key={store.store} store={store} />)}
        </div>
      ) : null}
      {data?.boxes.length ? (
        <SourcingList>
          {data.boxes.map((box) => (
            <BoxRow
              key={box.id}
              box={box}
              saving={update.isPending}
              onChange={(changes) => update.mutate({ id: box.id, changes })}
              onRemove={() => void onRemove(box)}
            />
          ))}
        </SourcingList>
      ) : null}
    </PageSection>
  );
}

function StoreCard({ store }: { store: SourceStoreResults }) {
  return (
    <Panel divided={false} className="flex flex-col gap-1 p-4 text-xs text-muted-foreground">
      <strong className="text-sm font-medium text-foreground">{boxStoreName(store.store)}</strong>
      <span>{store.boxes === 1 ? "1 box" : `${store.boxes} boxes`} · {formatMoney(Math.round(store.spent))} in</span>
      <span>
        {formatMoney(Math.round(store.returned))} back · <Profit value={store.profit} />
      </span>
      <span>{formatPercent(store.sell_through)} sold{store.median_days != null ? ` · ${formatDays(store.median_days)} to sell` : ""}</span>
      {profitPerHundred(store.roi) ? <span>{profitPerHundred(store.roi)}</span> : null}
      {boxScorecard(store).map((part) => <span key={part}>{part}</span>)}
    </Panel>
  );
}

function BoxRow({
  box,
  saving,
  onChange,
  onRemove,
}: {
  box: SourceBox;
  saving: boolean;
  onChange: (changes: Partial<SourceBoxInput>) => void;
  onRemove: () => void;
}) {
  const money = (text: string) => Number.isFinite(Number(text)) && Number(text) >= 0 && text !== "";
  const bought = box.bought_at ? new Date(box.bought_at).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : null;
  return (
    <SourcingItem
      main={<>
        {box.url ? (
          <a className={sourcingTitleClassName} href={box.url} target="_blank" rel="noopener noreferrer">{box.title}</a>
        ) : (
          <span className="text-sm font-medium text-foreground">{box.title}</span>
        )}
        <div className={sourcingReasonClassName}>
          {[boxStoreName(box.store), bought ? `bought ${bought}` : null, boxProgress(box)].filter(Boolean).join(" · ")}
          {box.median_days != null ? ` · ${formatDays(box.median_days)} to sell` : ""}
        </div>
        {boxScorecard(box).length ? (
          <div className={sourcingReasonClassName}>{boxScorecard(box).join(" · ")}</div>
        ) : null}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-2">
          <SourcingSetting label="Box">
            <DraftInput
              value={String(box.price)}
              label={`Price paid for ${box.title}`}
              prefix="$"
              width="5rem"
              inputMode="decimal"
              disabled={saving}
              clean={(text) => text.replace(/[^\d.]/g, "")}
              accept={money}
              onCommit={(text) => onChange({ price: Number(text) })}
            />
          </SourcingSetting>
          <SourcingSetting label="Shipping">
            <DraftInput
              value={String(box.shipping)}
              label={`Shipping paid for ${box.title}`}
              prefix="$"
              width="5.5rem"
              inputMode="decimal"
              disabled={saving}
              clean={(text) => text.replace(/[^\d.]/g, "")}
              accept={money}
              onCommit={(text) => onChange({ shipping: Number(text) })}
            />
          </SourcingSetting>
          <SourcingSetting label="Pieces">
            <DraftInput
              value={box.pieces ? String(box.pieces) : ""}
              label={`Pieces in ${box.title}`}
              width="3.5rem"
              inputMode="numeric"
              disabled={saving}
              clean={(text) => text.replace(/\D/g, "")}
              accept={() => true}
              onCommit={(text) => onChange({ pieces: text ? Number(text) : null })}
            />
          </SourcingSetting>
          <SourcingSetting label="Unsellable">
            <DraftInput
              value={box.unsellable != null ? String(box.unsellable) : ""}
              label={`Pieces in ${box.title} not worth listing`}
              width="3.5rem"
              inputMode="numeric"
              disabled={saving}
              clean={(text) => text.replace(/\D/g, "")}
              accept={(text) => text === "" || !box.pieces || Number(text) <= box.pieces}
              onCommit={(text) => onChange({ unsellable: text ? Number(text) : null })}
            />
          </SourcingSetting>
          {box.cost_per_piece != null ? (
            <span className="text-xs text-muted-foreground">
              {formatMoney(box.cost_per_piece)} {box.unsellable ? "a sellable piece" : "a piece"}
            </span>
          ) : null}
          <InlineButton tone="destructive" onClick={onRemove}>Remove</InlineButton>
        </div>
      </>}
      money={<>
        <div>{formatMoney(Math.round(box.spent))} in</div>
        <div>{formatMoney(Math.round(box.returned))} back</div>
        <Profit value={box.profit} />
        {box.unsold_asking > 0 ? (
          <div className="text-xs text-muted-foreground">{formatMoney(Math.round(box.unsold_asking))} still listed</div>
        ) : null}
      </>}
    />
  );
}

function AddBoxForm({
  saving,
  onCancel,
  onSave,
}: {
  saving: boolean;
  onCancel: () => void;
  onSave: (box: SourceBoxInput) => void;
}) {
  const [store, setStore] = useState("Raghouse");
  const [title, setTitle] = useState("");
  const [price, setPrice] = useState("");
  const [shipping, setShipping] = useState("");
  const [pieces, setPieces] = useState("");
  const valid = store.trim() && title.trim() && price !== "" && Number(price) >= 0 && Number(shipping || 0) >= 0;
  return (
    <Panel divided={false} className="p-4">
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (!valid || saving) return;
          onSave({
            store: boxStoreKey(store),
            title: title.trim(),
            price: Number(price),
            shipping: Number(shipping || 0),
            pieces: pieces ? Number(pieces) : null,
          });
        }}
      >
        <div className="grid grid-cols-2 gap-3 @2xl/page:grid-cols-[1fr_2fr_repeat(3,minmax(0,0.7fr))]">
          <FormField label="Store">
            <Input nativeInput size="sm" list="bought-stores" value={store} onChange={(event) => setStore(event.target.value)} />
            <datalist id="bought-stores">
              <option value="Raghouse" />
              <option value="Thrift Vintage Fashion" />
            </datalist>
          </FormField>
          <FormField label="Box" className="col-span-2 @2xl/page:col-span-1">
            <Input
              nativeInput
              size="sm"
              placeholder="Carhartt mix 25 pcs"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              autoFocus
            />
          </FormField>
          <FormField label="Price">
            <Input nativeInput size="sm" inputMode="decimal" value={price} onChange={(event) => setPrice(event.target.value.replace(/[^\d.]/g, ""))} />
          </FormField>
          <FormField label="Shipping">
            <Input nativeInput size="sm" inputMode="decimal" value={shipping} onChange={(event) => setShipping(event.target.value.replace(/[^\d.]/g, ""))} />
          </FormField>
          <FormField label="Pieces">
            <Input nativeInput size="sm" inputMode="numeric" value={pieces} onChange={(event) => setPieces(event.target.value.replace(/\D/g, ""))} />
          </FormField>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
          <Button type="submit" size="sm" disabled={!valid || saving}>Add box</Button>
        </div>
      </form>
    </Panel>
  );
}
