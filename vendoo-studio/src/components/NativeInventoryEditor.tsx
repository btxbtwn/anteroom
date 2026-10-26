import * as React from "react";
import type { NativeInventoryGroup, NativeInventoryRow } from "../api/types";
import { SearchableSelect } from "./ListingFormControls";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

export function NativeInventoryEditor({ value, groups, disabled, onChange }: {
  value: NativeInventoryRow[];
  groups: NativeInventoryGroup[];
  disabled?: boolean;
  onChange: (rows: NativeInventoryRow[]) => void;
}) {
  const [rows, setRows] = React.useState(value);
  const [source, setSource] = React.useState(value);
  if (source !== value) { setSource(value); setRows(value); }
  const update = (index: number, patch: Partial<NativeInventoryRow>, commit = false) => {
    const next = rows.map((row, at) => at === index ? { ...row, ...patch } : row);
    setRows(next);
    if (commit) onChange(next);
  };
  return <div className="space-y-3">
    <div className="space-y-3" role="group" aria-label="Size inventory">
        {rows.map((row, index) => {
          const group = groups.find(group => group.id === row.sizeSetId);
          const prefix = `inventory-row-${index}`;
          return <fieldset key={index} className="min-w-0 space-y-2 rounded-lg border p-3">
            <legend className="px-1 text-xs text-muted-foreground">Size {index + 1}</legend>
            <div className="grid min-w-0 grid-cols-2 gap-3">
            <div className="min-w-0"><label className="mb-1 block text-xs text-muted-foreground" htmlFor={`${prefix}-group`}>Size group</label><SearchableSelect id={`${prefix}-group`} fieldKey={`${prefix}-group`} value={group?.label || row.sizeSetId} options={groups.map(group => group.label)}
              selectionOnly disabled={disabled} choicesVerified onChange={label => update(index, {
                sizeSetId: groups.find(group => group.label === label)?.id || "", sizeId: "",
              }, true)} /></div>
            <div className="min-w-0"><label className="mb-1 block text-xs text-muted-foreground" htmlFor={`${prefix}-size`}>Size</label>{row.sizeSetId === "custom"
              ? <Input nativeInput id={`${prefix}-size`} aria-label={`Custom size ${index + 1}`} value={row.sizeId} disabled={disabled}
                  onChange={event => update(index, { sizeId: event.target.value })} onBlur={() => onChange(rows)} />
              : <SearchableSelect id={`${prefix}-size`} fieldKey={`${prefix}-size`} value={group?.sizes.find(size => size.id === row.sizeId)?.label || row.sizeId}
                  options={group?.sizes.map(size => size.label) || []} selectionOnly disabled={disabled || !group} choicesVerified
                  onChange={label => update(index, { sizeId: group?.sizes.find(size => size.label === label)?.id || "" }, true)} />}</div>
            <div className="min-w-0"><label className="mb-1 block text-xs text-muted-foreground" htmlFor={`${prefix}-quantity`}>Quantity</label><Input nativeInput id={`${prefix}-quantity`} aria-label={`Quantity ${index + 1}`} type="number" min={1} step={1} value={row.quantity}
              disabled={disabled} onChange={event => update(index, { quantity: event.target.value })} onBlur={() => onChange(rows)} /></div>
            <div className="min-w-0"><label className="mb-1 block text-xs text-muted-foreground" htmlFor={`${prefix}-sku`}>SKU (optional)</label><Input nativeInput id={`${prefix}-sku`} aria-label={`SKU ${index + 1}`} value={row.sku || ""} maxLength={50}
              disabled={disabled} onChange={event => update(index, { sku: event.target.value })} onBlur={() => onChange(rows)} /></div>
            </div>
            <Button type="button" variant="ghost" size="sm" disabled={disabled} aria-label={`Remove size ${index + 1}`}
              onClick={() => { const next = rows.filter((_, at) => at !== index); setRows(next); onChange(next); }}>Remove</Button>
          </fieldset>;
        })}
    </div>
    <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => {
      const next = [...rows, { sizeSetId: rows[0]?.sizeSetId || groups[0]?.id || "custom", sizeId: "", quantity: 1, sku: "" }];
      setRows(next); onChange(next);
    }}>Add size</Button>
  </div>;
}
