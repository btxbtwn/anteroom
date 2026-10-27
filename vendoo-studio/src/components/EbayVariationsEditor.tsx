import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EbayVariationAspect, EbayVariationRow, EbayVariations } from "../api/types";
import { SearchableMultiSelect, SearchableSelect } from "./ListingFormControls";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

/** eBay allows five aspects to vary and twelve photos for each value of the photo aspect. */
export const MAX_VARIATION_ASPECTS = 5;
export const MAX_VARIATION_PHOTOS = 12;

export function emptyVariations(): EbayVariations {
  return { aspects: [], rows: [], photos: {} };
}

/** The listing's eBay variations in their stored shape, whatever partial value it held. */
export function normalizeVariations(value: unknown): EbayVariations {
  if (!value || typeof value !== "object" || Array.isArray(value)) return emptyVariations();
  const raw = value as Partial<EbayVariations>;
  return {
    aspects: Array.isArray(raw.aspects) ? raw.aspects.filter((name): name is string => typeof name === "string") : [],
    ...(raw.photosBy ? { photosBy: raw.photosBy } : {}),
    rows: Array.isArray(raw.rows) ? raw.rows.filter((row): row is EbayVariationRow => Boolean(row) && typeof row === "object") : [],
    photos: raw.photos && typeof raw.photos === "object" && !Array.isArray(raw.photos) ? raw.photos : {},
  };
}

/** Units across every variation; blank or invalid quantities count as none. */
export function variationTotal(value: EbayVariations): number {
  return value.rows.reduce((total, row) => {
    const quantity = Number(row.quantity);
    return total + (Number.isSafeInteger(quantity) && quantity > 0 ? quantity : 0);
  }, 0);
}

/** Change what the variations differ by. Rows keep the values they already had for aspects that stay. */
export function withAspects(value: EbayVariations, aspects: string[]): EbayVariations {
  const rows = value.rows.map(row => ({
    ...row,
    values: Object.fromEntries(aspects.map(name => [name, row.values?.[name] ?? ""])),
  }));
  const photosBy = value.photosBy && aspects.includes(value.photosBy) ? value.photosBy : undefined;
  // Photos are kept per value of one aspect; they stay only while that aspect does.
  const same = (photosBy || aspects[0]) === (value.photosBy || value.aspects[0]);
  return { aspects, rows, photos: same ? value.photos : {}, ...(photosBy ? { photosBy } : {}) };
}

export function EbayVariationsEditor({ convId, value, aspects, disabled, onChange }: {
  convId: string;
  value: EbayVariations;
  aspects: EbayVariationAspect[];
  disabled?: boolean;
  onChange: (value: EbayVariations) => void;
}) {
  const [draft, setDraft] = React.useState(value);
  const [source, setSource] = React.useState(value);
  if (source !== value) { setSource(value); setDraft(value); }
  const { data: photos } = useQuery({ queryKey: ["photos", convId], queryFn: () => api.conversations.photos(convId) });
  const commit = (next: EbayVariations) => { setDraft(next); onChange(next); };
  const updateRow = (index: number, patch: Partial<EbayVariationRow>, save = false) => {
    const next = { ...draft, rows: draft.rows.map((row, at) => at === index ? { ...row, ...patch } : row) };
    if (save) commit(next); else setDraft(next);
  };
  const byName = new Map(aspects.map(aspect => [aspect.name, aspect]));
  const photosBy = draft.photosBy || draft.aspects[0] || "";
  const photoValues = [...new Set(draft.rows.map(row => row.values?.[photosBy] || "").filter(Boolean))];
  const total = variationTotal(draft);
  return <div className="space-y-3">
    <div className="min-w-0">
      <label className="mb-1 block text-xs text-muted-foreground" htmlFor="ebay-variation-aspects">Variations differ by</label>
      <SearchableMultiSelect id="ebay-variation-aspects" fieldKey="ebay-variation-aspects" value={draft.aspects}
        options={aspects.map(aspect => aspect.name)} maxValues={MAX_VARIATION_ASPECTS} disabled={disabled} choicesVerified
        onChange={names => commit(withAspects(draft, names.slice(0, MAX_VARIATION_ASPECTS)))} />
      {!aspects.length && <p className="mt-1 text-xs text-muted-foreground">eBay lists no aspects that can vary in this category.</p>}
    </div>
    {draft.aspects.length > 0 && <div className="space-y-3" role="group" aria-label="eBay variations">
      {draft.rows.map((row, index) => {
        const prefix = `ebay-variation-${index}`;
        return <fieldset key={index} className="min-w-0 space-y-2 rounded-lg border p-3">
          <legend className="px-1 text-xs text-muted-foreground">Variation {index + 1}</legend>
          <div className="grid min-w-0 grid-cols-2 gap-3">
            {draft.aspects.map(name => {
              const aspect = byName.get(name);
              const id = `${prefix}-${name}`;
              const current = row.values?.[name] || "";
              return <div key={name} className="min-w-0">
                <label className="mb-1 block text-xs text-muted-foreground" htmlFor={id}>{name}</label>
                {aspect?.options.length
                  ? <SearchableSelect id={id} fieldKey={id} value={current} options={aspect.options}
                      selectionOnly={aspect.selection_only} disabled={disabled} choicesVerified
                      onChange={text => updateRow(index, { values: { ...row.values, [name]: text } }, true)} />
                  : <Input nativeInput id={id} aria-label={`${name} ${index + 1}`} value={current} maxLength={50} disabled={disabled}
                      onChange={event => updateRow(index, { values: { ...row.values, [name]: event.target.value } })}
                      onBlur={() => onChange(draft)} />}
              </div>;
            })}
            <div className="min-w-0"><label className="mb-1 block text-xs text-muted-foreground" htmlFor={`${prefix}-quantity`}>Quantity</label>
              <Input nativeInput id={`${prefix}-quantity`} aria-label={`Variation quantity ${index + 1}`} type="number" min={0} max={10000} step={1}
                value={row.quantity} disabled={disabled} onChange={event => updateRow(index, { quantity: event.target.value })} onBlur={() => onChange(draft)} /></div>
            <div className="min-w-0"><label className="mb-1 block text-xs text-muted-foreground" htmlFor={`${prefix}-price`}>Price (optional)</label>
              <Input nativeInput id={`${prefix}-price`} aria-label={`Variation price ${index + 1}`} type="number" min={0} step="0.01" placeholder="Listing price"
                value={row.price ?? ""} disabled={disabled} onChange={event => updateRow(index, { price: event.target.value })} onBlur={() => onChange(draft)} /></div>
            <div className="min-w-0"><label className="mb-1 block text-xs text-muted-foreground" htmlFor={`${prefix}-sku`}>SKU (optional)</label>
              <Input nativeInput id={`${prefix}-sku`} aria-label={`Variation SKU ${index + 1}`} maxLength={50} placeholder="Made from the item SKU"
                value={row.sku ?? ""} disabled={disabled} onChange={event => updateRow(index, { sku: event.target.value })} onBlur={() => onChange(draft)} /></div>
          </div>
          <Button type="button" variant="ghost" size="sm" disabled={disabled} aria-label={`Remove variation ${index + 1}`}
            onClick={() => commit({ ...draft, rows: draft.rows.filter((_, at) => at !== index) })}>Remove</Button>
        </fieldset>;
      })}
      <div className="flex items-center justify-between gap-3">
        <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => commit({
          ...draft, rows: [...draft.rows, { values: Object.fromEntries(draft.aspects.map(name => [name, ""])), quantity: 1, sku: "", price: "" }],
        })}>Add variation</Button>
        <span className="text-xs text-muted-foreground">{draft.rows.length} variations · {total} in stock</span>
      </div>
    </div>}
    {draft.aspects.length > 0 && photoValues.length > 0 && <div className="space-y-2">
      <div className="min-w-0">
        <label className="mb-1 block text-xs text-muted-foreground" htmlFor="ebay-variation-photos-by">Photos differ by</label>
        <SearchableSelect id="ebay-variation-photos-by" fieldKey="ebay-variation-photos-by" value={photosBy} options={draft.aspects}
          disabled={disabled} choicesVerified onChange={name => commit({ ...draft, photosBy: name, photos: name === photosBy ? draft.photos : {} })} />
      </div>
      {photoValues.map(text => {
        const chosen = draft.photos[text] || [];
        return <div key={text} className="space-y-1" role="group" aria-label={`Photos for ${text}`}>
          <div className="text-xs text-muted-foreground">{text} · {chosen.length}/{MAX_VARIATION_PHOTOS}</div>
          <div className="flex flex-wrap gap-1.5">
            {(photos || []).map(photo => {
              const on = chosen.includes(photo.id);
              return <button key={photo.id} type="button" aria-pressed={on} aria-label={`${on ? "Remove" : "Use"} ${photo.original_filename} for ${text}`}
                disabled={disabled || (!on && chosen.length >= MAX_VARIATION_PHOTOS)}
                className={`size-12 overflow-hidden rounded-md border-2 ${on ? "border-primary" : "border-transparent opacity-60"}`}
                onClick={() => commit({ ...draft, photos: { ...draft.photos, [text]: on ? chosen.filter(id => id !== photo.id) : [...chosen, photo.id] } })}>
                <img src={photo.url} alt="" className="size-full object-cover" />
              </button>;
            })}
          </div>
        </div>;
      })}
    </div>}
  </div>;
}
