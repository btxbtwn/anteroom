/**
 * Above the listing form: the warnings the seller asked for and the one-click
 * fixes for them. Apply pricing rules and Apply shipping preset change the
 * local listing through the editor's own save; nothing is sent to a
 * marketplace, and a live listing shows unsent edits until the seller updates it.
 */
import { useMutation, useQuery } from "@tanstack/react-query";
import { PackageIcon, ScaleIcon, TriangleAlertIcon } from "lucide-react";
import { api } from "../api/client";
import { presetSummary } from "../api/listingRules";
import type { ListingData } from "../api/types";
import type { PriceDrift } from "../api/contract.gen";
import { formatMoney } from "./analyticsFormat";
import { marketplaceName } from "./marketplaceNames";
import { Button } from "./ui/button";
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "./ui/menu";

/** The warning lines this listing shows, in the order the seller reads them. */
export function guardrailWarnings({ listing, drift, warnMissingSku, inactive }: {
  listing: ListingData;
  drift: readonly PriceDrift[];
  warnMissingSku: boolean;
  inactive: readonly string[];
}): { id: string; text: string }[] {
  const warnings: { id: string; text: string }[] = [];
  if (drift.length) {
    warnings.push({
      id: "price",
      text: `Price inconsistency: ${drift.map((row) => `${marketplaceName(row.marketplace)} ${formatMoney(row.price)} (rules say ${formatMoney(row.expected)})`).join(", ")}`,
    });
  }
  if (warnMissingSku && !String(listing.sku ?? "").trim()) warnings.push({ id: "sku", text: "This item has no SKU." });
  if (inactive.length) {
    warnings.push({ id: "inactive", text: `Inactive on ${inactive.map(marketplaceName).join(", ")}: still up there, but not for sale.` });
  }
  return warnings;
}

export function ListingGuardrails({ listing, drift = [], inactive = [], disabled, onChange }: {
  listing: ListingData;
  drift?: readonly PriceDrift[];
  inactive?: readonly string[];
  disabled: boolean;
  /** The editor's save: the same path as any field edit, with stale-edit protection. */
  onChange: (listing: ListingData) => void;
}) {
  const { data: rules } = useQuery({ queryKey: ["settings-pricing-rules"], queryFn: api.settings.pricingRules });
  const { data: presets } = useQuery({ queryKey: ["settings-shipping-presets"], queryFn: api.settings.shippingPresets });
  const { data: warningSettings } = useQuery({ queryKey: ["settings-listing-warnings"], queryFn: api.settings.listingWarnings });
  const applyRules = useMutation({
    mutationFn: () => api.settings.applyPricingRules(listing),
    onSuccess: (result) => onChange(result.listing),
  });
  const applyPreset = useMutation({
    mutationFn: (id: string) => api.settings.applyShippingPreset(id, listing),
    onSuccess: (result) => onChange(result.listing),
  });
  const warnings = guardrailWarnings({ listing, drift, warnMissingSku: Boolean(warningSettings?.missing_sku), inactive });
  const presetList = presets?.presets || [];
  const busy = disabled || applyRules.isPending || applyPreset.isPending;
  const failure = applyRules.error || applyPreset.error;
  if (!warnings.length && !presetList.length && !rules?.enabled) return null;
  return (
    <div className="flex flex-col gap-1.5" aria-label="Listing checks">
      {warnings.map((warning) => (
        <p key={warning.id} role="status" className="flex items-start gap-1.5 text-xs text-warning-foreground">
          <TriangleAlertIcon className="mt-px size-3.5 shrink-0" aria-hidden="true" />
          <span className="min-w-0 [overflow-wrap:anywhere]">{warning.text}</span>
        </p>
      ))}
      <div className="flex flex-wrap items-center gap-1.5">
        {rules?.enabled ? (
          <Button size="xs" variant={drift.length ? "outline" : "ghost-muted"} disabled={busy}
            title="Put every marketplace price back on your pricing rules. Saves locally; sends nothing."
            onClick={() => applyRules.mutate()}>
            <ScaleIcon />
            {applyRules.isPending ? "Applying…" : "Apply pricing rules"}
          </Button>
        ) : null}
        {presetList.length ? (
          <Menu>
            <MenuTrigger render={<Button size="xs" variant="ghost-muted" disabled={busy} />}>
              <PackageIcon />
              {applyPreset.isPending ? "Applying…" : "Apply shipping preset"}
            </MenuTrigger>
            <MenuPopup align="start" className="min-w-56">
              {presetList.map((preset) => (
                <MenuItem key={preset.id} onClick={() => applyPreset.mutate(preset.id)}>
                  <span className="flex min-w-0 flex-col">
                    <span className="truncate">{preset.name}</span>
                    <span className="truncate text-2xs text-muted-foreground">{preset.usage_condition || presetSummary(preset)}</span>
                  </span>
                </MenuItem>
              ))}
            </MenuPopup>
          </Menu>
        ) : null}
      </div>
      {failure ? <p role="alert" className="text-xs text-destructive-foreground">{failure.message}</p> : null}
    </div>
  );
}
