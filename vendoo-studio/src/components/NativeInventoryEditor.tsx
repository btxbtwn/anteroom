import * as React from "react";
import type { NativeInventoryGroup, NativeInventoryRow } from "../api/types";
import { SearchableSelect, SegmentedTabs } from "./ListingFormControls";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

const SIZE_SYSTEMS = ["us", "au", "eu", "uk"] as const;

function sizesFor(group: NativeInventoryGroup | undefined, system: string) {
  return group?.systems?.[system] || group?.sizes || [];
}

function remapRow(row: NativeInventoryRow, groups: NativeInventoryGroup[], from: string, to: string): NativeInventoryRow {
  if (row.sizeSetId === "custom" || from === to) return row;
  const current = sizesFor(groups.find(group => group.id === row.sizeSetId), from).find(size => size.id === row.sizeId);
  return { ...row, sizeId: current?.equivalents?.[to] || "" };
}

/** Poshmark's size picker: one size for Single Item, a quantity table for Multi Item, and the US/AU/EU/UK switch. */
export function NativeInventoryEditor({ value, groups, disabled, onChange, multi = true, sizeSystem = "us", onSizeSystem }: {
  value: NativeInventoryRow[];
  groups: NativeInventoryGroup[];
  disabled?: boolean;
  onChange: (rows: NativeInventoryRow[]) => void;
  /** False keeps one size at quantity 1, the way Poshmark's Single Item does. */
  multi?: boolean;
  sizeSystem?: string;
  onSizeSystem?: (system: string, rows: NativeInventoryRow[]) => void;
}) {
  const shown = multi ? value : value.slice(0, 1);
  const [rows, setRows] = React.useState(shown.length ? shown : [{ sizeSetId: groups[0]?.id || "custom", sizeId: "", quantity: 1, sku: "" }]);
  const [source, setSource] = React.useState(value);
  const [system, setSystem] = React.useState(sizeSystem);
  const [skuOpen, setSkuOpen] = React.useState(value.some(row => String(row.sku || "").trim() !== ""));
  if (source !== value || system !== sizeSystem) {
    setSource(value);
    setSystem(sizeSystem);
    const next = multi ? value : value.slice(0, 1);
    setRows(next.length ? next : [{ sizeSetId: groups[0]?.id || "custom", sizeId: "", quantity: 1, sku: "" }]);
  }
  const systems = groups.some(group => group.systems && Object.keys(group.systems).length > 0) ? [...SIZE_SYSTEMS] : [];
  const commit = (next: NativeInventoryRow[]) => {
    const saved = multi ? next : [{ ...(next[0] || { sizeSetId: "", sizeId: "", quantity: 1 }), quantity: 1 }];
    setRows(saved);
    onChange(saved);
  };
  const update = (index: number, patch: Partial<NativeInventoryRow>, save = false) => {
    const next = rows.map((row, at) => at === index ? { ...row, ...patch } : row);
    setRows(next);
    if (save) commit(next);
  };
  const switchSystem = (next: string) => {
    const remapped = rows.map(row => remapRow(row, groups, system, next));
    setSystem(next);
    setRows(remapped);
    if (onSizeSystem) onSizeSystem(next, multi ? remapped : [{ ...(remapped[0] || { sizeSetId: "", sizeId: "", quantity: 1 }), quantity: 1 }]);
    else commit(remapped);
  };
  const systemSwitch = systems.length > 1 ? (
    <SegmentedTabs tabs={systems.map(id => ({ id, label: id.toUpperCase() }))} value={systems.includes(system as typeof systems[number]) ? system : systems[0]}
      onChange={switchSystem} label="Sizing" />
  ) : null;
  const sizeFields = (row: NativeInventoryRow, index: number) => {
    const group = groups.find(entry => entry.id === row.sizeSetId);
    const choices = sizesFor(group, system);
    const prefix = `inventory-row-${index}`;
    return <>
      <div className="min-w-0"><label className="mb-1 block text-xs text-muted-foreground" htmlFor={`${prefix}-group`}>Size group</label><SearchableSelect id={`${prefix}-group`} fieldKey={`${prefix}-group`} value={group?.label || row.sizeSetId} options={groups.map(entry => entry.label)}
        selectionOnly disabled={disabled} choicesVerified onChange={label => update(index, {
          sizeSetId: groups.find(entry => entry.label === label)?.id || "", sizeId: "",
        }, true)} /></div>
      <div className="min-w-0"><label className="mb-1 block text-xs text-muted-foreground" htmlFor={`${prefix}-size`}>Size</label>{row.sizeSetId === "custom"
        ? <Input nativeInput id={`${prefix}-size`} aria-label={`Custom size ${index + 1}`} value={row.sizeId} disabled={disabled}
            onChange={event => update(index, { sizeId: event.target.value })} onBlur={() => commit(rows)} />
        : <SearchableSelect id={`${prefix}-size`} fieldKey={`${prefix}-size`} value={choices.find(size => size.id === row.sizeId)?.label || row.sizeId}
            options={choices.map(size => size.label)} selectionOnly disabled={disabled || !group} choicesVerified
            onChange={label => update(index, { sizeId: choices.find(size => size.label === label)?.id || "" }, true)} />}</div>
    </>;
  };
  return <div className="space-y-3">
    {systemSwitch}
    {multi ? <div className="space-y-3" role="group" aria-label="Size inventory">
      <div className="overflow-x-auto">
        <table className="w-full min-w-0 text-left text-sm">
          <thead><tr className="text-xs text-muted-foreground">
            <th className="pb-1 pr-3 font-medium">Size</th>
            <th className="pb-1 pr-3 font-medium">Available</th>
            <th className="pb-1 pr-3 font-medium">Sold</th>
            {skuOpen && <th className="pb-1 font-medium">Item SKU</th>}
          </tr></thead>
          <tbody>
            {rows.map((row, index) => <tr key={index} className="align-top">
              <td className="py-1 pr-3"><div className="grid min-w-0 grid-cols-2 gap-2">{sizeFields(row, index)}</div></td>
              <td className="py-1 pr-3"><Input nativeInput aria-label={`Quantity ${index + 1}`} type="number" min={1} step={1} placeholder="Quantity" value={row.quantity}
                disabled={disabled} onChange={event => update(index, { quantity: event.target.value })} onBlur={() => commit(rows)} /></td>
              <td className="py-1 pr-3 text-muted-foreground">0</td>
              {skuOpen && <td className="py-1"><Input nativeInput aria-label={`SKU ${index + 1}`} value={row.sku || ""} maxLength={50}
                disabled={disabled} onChange={event => update(index, { sku: event.target.value })} onBlur={() => commit(rows)} /></td>}
              <td className="py-1 pl-2"><Button type="button" variant="ghost" size="sm" disabled={disabled || rows.length < 2} aria-label={`Remove size ${index + 1}`}
                onClick={() => commit(rows.filter((_, at) => at !== index))}>Remove</Button></td>
            </tr>)}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => commit([...rows, { sizeSetId: rows[0]?.sizeSetId || groups[0]?.id || "custom", sizeId: "", quantity: 1, sku: "" }])}>Add another size</Button>
        <Button type="button" variant="ghost" size="sm" disabled={disabled} aria-pressed={skuOpen} onClick={() => setSkuOpen(open => !open)}>Add item SKUs</Button>
      </div>
    </div> : <div role="group" aria-label="Size inventory" className="grid max-w-full gap-3 sm:max-w-[50%] sm:grid-cols-2">
      {sizeFields(rows[0] || { sizeSetId: "", sizeId: "", quantity: 1 }, 0)}
    </div>}
  </div>;
}
