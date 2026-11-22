/** Pricing rules, shipping presets and listing warnings: seller settings that change only local listings. */
import type { ListingData } from "./types";

export const RULE_MARKETPLACES = ["ebay", "poshmark", "mercari", "depop", "etsy"] as const;
export type RuleMarketplace = (typeof RULE_MARKETPLACES)[number];
export type PriceAnchor = "general" | RuleMarketplace;
export type PriceRounding = "none" | "99" | "whole";

export interface PricingRule { percent: number; fixed: number; rounding: PriceRounding }

export interface PricingRules {
  ok: boolean;
  enabled: boolean;
  anchor: PriceAnchor;
  rules: Record<RuleMarketplace, PricingRule>;
  defaults: Record<RuleMarketplace, PricingRule>;
  fees: Record<RuleMarketplace, string>;
}

export interface PricingRulesUpdate {
  enabled?: boolean;
  anchor?: PriceAnchor;
  rules?: Partial<Record<RuleMarketplace, PricingRule>>;
  reset?: boolean;
}

export interface ShippingPreset {
  id: string;
  name: string;
  usage_condition: string;
  length: number | null;
  width: number | null;
  height: number | null;
  weight_lb: number | null;
  weight_oz: number | null;
  marketplaces: Partial<Record<string, Record<string, string>>>;
}

export type ShippingPresetInput = Omit<ShippingPreset, "id">;

export interface PresetField { label: string; choices?: Record<string, string> }

export interface ShippingPresets {
  ok: boolean;
  presets: ShippingPreset[];
  fields: Record<string, Record<string, PresetField>>;
}

export interface ListingWarnings { ok: boolean; missing_sku: boolean }

export interface TransformedListing { listing: ListingData; changed?: string[]; paths?: string[] }

export type TextMode = "keep" | "shorten" | "rewrite";

export const TEXT_MODES: { id: TextMode; label: string; detail: string }[] = [
  { id: "keep", label: "Keep as-is", detail: "Each marketplace uses the General title and description." },
  { id: "shorten", label: "Shorten with AI", detail: "Only a title or description over a marketplace's limit is shortened." },
  { id: "rewrite", label: "Write with AI", detail: "AI writes each marketplace its own title and description." },
];

export interface MarketplaceTextResult {
  conversation_id: string;
  revision_id: string;
  changes: { marketplace: string; field: string; old: string; new: string }[];
  skipped: { marketplace: string; field: string; reason: string }[];
}

/** A short summary of a preset's package, for menus: "14×10×6 in · 3 lb". */
export function presetSummary(preset: ShippingPreset): string {
  const parts: string[] = [];
  if (preset.length != null) parts.push(`${preset.length}×${preset.width}×${preset.height} in`);
  if (preset.weight_lb != null || preset.weight_oz != null) {
    const pounds = preset.weight_lb || 0;
    const ounces = preset.weight_oz || 0;
    parts.push([pounds ? `${pounds} lb` : "", ounces ? `${ounces} oz` : ""].filter(Boolean).join(" ") || "0 oz");
  }
  const markets = Object.keys(preset.marketplaces || {});
  if (markets.length) parts.push(`${markets.length} marketplace${markets.length === 1 ? "" : "s"}`);
  return parts.join(" · ");
}
