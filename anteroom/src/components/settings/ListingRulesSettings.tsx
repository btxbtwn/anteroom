/**
 * Settings → Listings: pricing rules, shipping presets and listing warnings.
 * Each changes only local listings; nothing here lists, updates or delists.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PencilIcon, PlusIcon, RotateCcwIcon, Trash2Icon } from "lucide-react";
import { api } from "../../api/client";
import {
  RULE_MARKETPLACES,
  presetSummary,
  type PriceAnchor,
  type PriceRounding,
  type PricingRule,
  type RuleMarketplace,
  type ShippingPreset,
  type ShippingPresetInput,
  type ShippingPresets,
} from "../../api/listingRules";
import { marketplaceName } from "../marketplaceNames";
import { MarketplaceLogo } from "../MarketplaceLogo";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Spinner } from "../ui/spinner";
import { Switch } from "../ui/switch";
import { Textarea } from "../ui/textarea";
import { SettingsNote, SettingsRow, SettingsSection, SettingsSelect } from "./SettingsLayout";

const ROUNDING_OPTIONS: { value: PriceRounding; label: string }[] = [
  { value: "none", label: "Exact cents" },
  { value: "99", label: "Ends in .99" },
  { value: "whole", label: "Whole dollars (.00)" },
];

function errorText(failure: unknown): string {
  return failure instanceof Error ? failure.message : "Could not save.";
}

/** The marketplace price from an example item price, as the rule computes it (rounding up). */
export function examplePrice(anchor: number, rule: PricingRule, market: RuleMarketplace): string {
  const raw = Math.round((anchor * (1 + rule.percent / 100) + rule.fixed) * 100) / 100;
  if (raw <= 0) return "—";
  const whole = market === "poshmark" || rule.rounding === "whole";
  const priced = whole ? Math.ceil(raw) : rule.rounding === "99" ? Math.ceil(raw + 0.01) - 0.01 : raw;
  return `$${priced.toFixed(whole ? 0 : 2)}`;
}

export function PricingRulesSection() {
  const queryClient = useQueryClient();
  const { data, isPending, error } = useQuery({ queryKey: ["settings-pricing-rules"], queryFn: api.settings.pricingRules });
  const [drafts, setDrafts] = useState<Partial<Record<RuleMarketplace, { percent: string; fixed: string }>>>({});
  const save = useMutation({
    mutationFn: api.settings.setPricingRules,
    onSuccess: (payload) => {
      queryClient.setQueryData(["settings-pricing-rules"], payload);
      setDrafts({});
      // Drift warnings on every row follow the new rules.
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
  const busy = isPending || save.isPending;
  const anchor = data?.anchor ?? "general";
  const commit = (market: RuleMarketplace, change: Partial<PricingRule>) => {
    if (!data) return;
    const draft = drafts[market];
    const rule = data.rules[market];
    const percent = Number(change.percent ?? draft?.percent ?? rule.percent);
    const fixed = Number(change.fixed ?? draft?.fixed ?? rule.fixed);
    if (!Number.isFinite(percent) || !Number.isFinite(fixed)) return;
    save.mutate({ rules: { [market]: { percent, fixed, rounding: change.rounding ?? rule.rounding } } });
  };
  return (
    <SettingsSection id="pricing-rules" title="Pricing rules">
      <SettingsRow
        id="pricing-rules-enabled"
        title="Price each marketplace from one price"
        description="Marketplace prices you have not typed follow your main price through these rules, which start from typical selling fees so you keep about the same anywhere. A price you type on a marketplace stays yours. Rules change local prices only; live listings show unsent edits until you choose Update."
        inlineControl
        control={<Switch aria-label="Use pricing rules" checked={Boolean(data?.enabled)} disabled={busy || !data}
          onCheckedChange={(checked) => save.mutate({ enabled: checked === true })} />}
      />
      <SettingsRow
        id="pricing-rules-anchor"
        title="Main price"
        description="The price the rules start from."
        control={<SettingsSelect<PriceAnchor> aria-label="Main price" value={anchor} disabled={busy || !data}
          onChange={(value) => save.mutate({ anchor: value })}
          options={[{ value: "general", label: "Item price (General)" },
            ...RULE_MARKETPLACES.map((market) => ({ value: market as PriceAnchor, label: `${marketplaceName(market)} price` }))]} />}
      />
      <SettingsRow
        id="pricing-rules-marketplaces"
        title="Marketplace rules"
        description="Percent adjustment, then a dollar offset, then the ending. Poshmark always uses whole dollars."
        control={<Button size="sm" variant="outline" disabled={busy || !data} onClick={() => save.mutate({ reset: true })}>
          <RotateCcwIcon />Use typical fees</Button>}
      >
        {error ? <SettingsNote tone="error">{errorText(error)}</SettingsNote> : null}
        {data ? (
          <div className="grid gap-2">
            {RULE_MARKETPLACES.filter((market) => market !== anchor).map((market) => {
              const rule = data.rules[market];
              const draft = drafts[market];
              return (
                <div key={market} className="grid min-w-0 grid-cols-2 gap-2 rounded-lg border border-border/60 p-2.5 sm:grid-cols-[9rem_5.5rem_5.5rem_minmax(9rem,1fr)] sm:items-center">
                  <div className="col-span-2 flex min-w-0 items-center gap-2 sm:col-span-1">
                    <MarketplaceLogo id={market} label="" size={16} />
                    <span className="min-w-0 truncate text-sm font-medium">{marketplaceName(market)}</span>
                  </div>
                  <label className="grid gap-0.5 text-2xs text-muted-foreground">%
                    <Input size="sm" inputMode="decimal" aria-label={`${marketplaceName(market)} percent adjustment`}
                      value={draft?.percent ?? String(rule.percent)} disabled={busy}
                      onChange={(event) => setDrafts({ ...drafts, [market]: { percent: event.target.value, fixed: draft?.fixed ?? String(rule.fixed) } })}
                      onBlur={() => draft && commit(market, {})} />
                  </label>
                  <label className="grid gap-0.5 text-2xs text-muted-foreground">$ offset
                    <Input size="sm" inputMode="decimal" aria-label={`${marketplaceName(market)} dollar offset`}
                      value={draft?.fixed ?? String(rule.fixed)} disabled={busy}
                      onChange={(event) => setDrafts({ ...drafts, [market]: { percent: draft?.percent ?? String(rule.percent), fixed: event.target.value } })}
                      onBlur={() => draft && commit(market, {})} />
                  </label>
                  <div className="col-span-2 grid gap-0.5 text-2xs text-muted-foreground sm:col-span-1">
                    <span>Ending · $25 → {examplePrice(25, rule, market)}</span>
                    <SettingsSelect<PriceRounding> aria-label={`${marketplaceName(market)} price ending`} className="sm:w-full"
                      value={market === "poshmark" ? "whole" : rule.rounding} disabled={busy || market === "poshmark"}
                      onChange={(value) => commit(market, { rounding: value })} options={ROUNDING_OPTIONS} />
                  </div>
                  <p className="col-span-2 text-2xs text-muted-foreground sm:col-span-4">Typical fees: {data.fees[market]}</p>
                </div>
              );
            })}
          </div>
        ) : isPending ? <SettingsNote>Loading pricing rules…</SettingsNote> : null}
        {save.isError ? <SettingsNote tone="error">{errorText(save.error)}</SettingsNote> : null}
      </SettingsRow>
    </SettingsSection>
  );
}

const EMPTY_PRESET: ShippingPresetInput = {
  name: "", usage_condition: "", length: null, width: null, height: null, weight_lb: null, weight_oz: null, marketplaces: {},
};

function numberOrNull(text: string): number | null {
  const trimmed = text.trim();
  return trimmed === "" ? null : Number(trimmed);
}

function PresetForm({ initial, fields, busy, onSave, onCancel }: {
  initial: ShippingPresetInput;
  fields: ShippingPresets["fields"];
  busy: boolean;
  onSave: (preset: ShippingPresetInput) => void;
  onCancel: () => void;
}) {
  const [preset, setPreset] = useState<ShippingPresetInput>(initial);
  const number = (key: "length" | "width" | "height" | "weight_lb" | "weight_oz", label: string) => (
    <label className="grid gap-0.5 text-2xs text-muted-foreground">{label}
      <Input size="sm" inputMode="numeric" aria-label={label} value={preset[key] ?? ""} disabled={busy}
        onChange={(event) => setPreset({ ...preset, [key]: numberOrNull(event.target.value) })} />
    </label>
  );
  const setMarket = (market: string, key: string, value: string) => {
    const current = { ...preset.marketplaces[market] };
    if (value) current[key] = value; else delete current[key];
    const marketplaces = { ...preset.marketplaces, [market]: current };
    if (!Object.keys(current).length) delete marketplaces[market];
    setPreset({ ...preset, marketplaces });
  };
  return (
    <form className="grid gap-3 rounded-lg border border-border/60 p-3" onSubmit={(event) => { event.preventDefault(); onSave(preset); }}>
      <label className="grid gap-0.5 text-xs text-muted-foreground">Name
        <Input size="sm" aria-label="Preset name" value={preset.name} maxLength={60} disabled={busy}
          onChange={(event) => setPreset({ ...preset, name: event.target.value })} placeholder="Shoes" />
      </label>
      <label className="grid gap-0.5 text-xs text-muted-foreground">When to use it
        <Textarea size="sm" rows={2} aria-label="When to use this preset" value={preset.usage_condition} maxLength={300} disabled={busy}
          onChange={(event) => setPreset({ ...preset, usage_condition: event.target.value })}
          placeholder="Use for lightweight tops under 1 lb" />
      </label>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
        {number("length", "Length (in)")}
        {number("width", "Width (in)")}
        {number("height", "Height (in)")}
        {number("weight_lb", "Pounds")}
        {number("weight_oz", "Ounces")}
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {Object.entries(fields).flatMap(([market, marketFields]) => Object.entries(marketFields).map(([key, field]) => {
          const value = preset.marketplaces[market]?.[key] ?? "";
          return (
            <div key={`${market}.${key}`} className="grid gap-0.5 text-2xs text-muted-foreground">
              <span>{field.label}</span>
              {field.choices ? (
                <SettingsSelect<string> aria-label={field.label} className="sm:w-full" value={value || "__none"} disabled={busy}
                  onChange={(next) => setMarket(market, key, next === "__none" ? "" : next)}
                  options={[{ value: "__none", label: "Leave as is" }, ...Object.entries(field.choices).map(([choice, label]) => ({ value: choice, label }))]} />
              ) : (
                <Input size="sm" inputMode="numeric" aria-label={field.label} value={value} disabled={busy}
                  onChange={(event) => setMarket(market, key, event.target.value.trim())} placeholder="Leave empty to keep" />
              )}
            </div>
          );
        }))}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={busy || !preset.name.trim()}>{busy ? <Spinner /> : null}Save preset</Button>
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

export function ShippingPresetsSection() {
  const queryClient = useQueryClient();
  const { data, isPending, error } = useQuery({ queryKey: ["settings-shipping-presets"], queryFn: api.settings.shippingPresets });
  const [editing, setEditing] = useState<ShippingPreset | "new" | null>(null);
  const done = (payload: ShippingPresets) => {
    queryClient.setQueryData(["settings-shipping-presets"], payload);
    setEditing(null);
  };
  const save = useMutation({
    mutationFn: ({ id, preset }: { id: string | null; preset: ShippingPresetInput }) =>
      id ? api.settings.updateShippingPreset(id, preset) : api.settings.createShippingPreset(preset),
    onSuccess: done,
  });
  const remove = useMutation({ mutationFn: api.settings.deleteShippingPreset, onSuccess: done });
  const busy = save.isPending || remove.isPending;
  const presets = data?.presets || [];
  return (
    <SettingsSection id="shipping-presets" title="Shipping presets">
      <SettingsRow
        id="shipping-presets-list"
        title="Shipping presets"
        description="A package size, weight and marketplace shipping choices, with a plain-English rule for when to use it. Generation picks the preset that fits each item; you can also apply one in the editor or Bulk Edit."
        control={<Button size="sm" variant="outline" disabled={busy || !data || editing !== null}
          onClick={() => { save.reset(); setEditing("new"); }}><PlusIcon />Add preset</Button>}
      >
        {error ? <SettingsNote tone="error">{errorText(error)}</SettingsNote> : null}
        {isPending ? <SettingsNote>Loading shipping presets…</SettingsNote> : null}
        {editing === "new" && data ? (
          <PresetForm initial={EMPTY_PRESET} fields={data.fields} busy={busy}
            onSave={(preset) => save.mutate({ id: null, preset })} onCancel={() => setEditing(null)} />
        ) : null}
        {presets.map((preset) => (editing !== "new" && editing?.id === preset.id && data ? (
          <PresetForm key={preset.id} initial={preset} fields={data.fields} busy={busy}
            onSave={(next) => save.mutate({ id: preset.id, preset: next })} onCancel={() => setEditing(null)} />
        ) : (
          <div key={preset.id} className="flex min-w-0 items-start gap-2 rounded-lg border border-border/60 p-2.5">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{preset.name}</p>
              <p className="text-xs text-muted-foreground [overflow-wrap:anywhere]">{preset.usage_condition || "No rule for when to use it"}</p>
              <p className="text-2xs text-muted-foreground">{presetSummary(preset)}</p>
            </div>
            <Button size="icon-sm" variant="ghost-muted" aria-label={`Edit ${preset.name}`} disabled={busy || editing !== null}
              onClick={() => { save.reset(); setEditing(preset); }}><PencilIcon /></Button>
            <Button size="icon-sm" variant="ghost-muted" aria-label={`Delete ${preset.name}`} disabled={busy || editing !== null}
              onClick={() => remove.mutate(preset.id)}><Trash2Icon /></Button>
          </div>
        )))}
        {data && !presets.length && editing === null ? <SettingsNote>No shipping presets yet.</SettingsNote> : null}
        {save.isError || remove.isError ? <SettingsNote tone="error">{errorText(save.error || remove.error)}</SettingsNote> : null}
      </SettingsRow>
    </SettingsSection>
  );
}

export function ListingWarningsSection() {
  const queryClient = useQueryClient();
  const { data, isPending } = useQuery({ queryKey: ["settings-listing-warnings"], queryFn: api.settings.listingWarnings });
  const save = useMutation({
    mutationFn: api.settings.setListingWarnings,
    onSuccess: (payload) => queryClient.setQueryData(["settings-listing-warnings"], payload),
  });
  return (
    <SettingsSection id="listing-warnings" title="Warnings">
      <SettingsRow
        id="missing-sku-warning"
        title="Warn when a SKU is missing"
        description="Flags items without a SKU in the listing library, the inventory table and the listing's checks, and adds a Missing SKU filter."
        inlineControl
        control={<Switch aria-label="Warn when a SKU is missing" checked={Boolean(data?.missing_sku)} disabled={isPending || save.isPending}
          onCheckedChange={(checked) => save.mutate({ missing_sku: checked === true })} />}
      />
      {save.isError ? <SettingsNote tone="error">{errorText(save.error)}</SettingsNote> : null}
    </SettingsSection>
  );
}
