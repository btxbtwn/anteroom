/** Map a Forms editor field key onto static Vendoo dropdown lists. */

import type { MarketplaceForm } from "./api/types";

export type DropdownForms = Record<string, Record<string, string[]>>;

/**
 * The static lists with this listing's category schema laid over them. A
 * category's own options win: Depop's kids sizes are ages, not the adult list.
 */
export function withSchemaOptions(
  forms: DropdownForms | undefined,
  schema: MarketplaceForm[] | undefined,
): DropdownForms | undefined {
  if (!schema?.length) return forms;
  const out: DropdownForms = { ...forms };
  for (const form of schema) {
    const bucket = { ...out[form.marketplace] };
    for (const field of form.fields) {
      if (!field.options?.length) continue;
      // Replace every static spelling of the field, then add the schema's own.
      for (const key of Object.keys(bucket)) {
        if (optionKey(key) === optionKey(field.key) || optionKey(key) === optionKey(field.label)) delete bucket[key];
      }
      bucket[field.key] = field.options;
      bucket[field.label] = field.options;
    }
    out[form.marketplace] = bucket;
  }
  return out;
}

/**
 * Every fix prompt says this: a listed dropdown is the whole menu, and a value
 * outside it has to be cleared rather than swapped for another invention.
 */
export const OPTIONS_RULE =
  "When a field lists options, every value you send for it must be copied from that list verbatim "
  + "(same spelling, spacing and capitalization). Anything outside the list — \"Other\", \"Mixed\", "
  + "\"Unknown\" — fails validation again. If nothing in the list fits, send that field with an empty "
  + "value (\"value\": \"\") to clear whatever is there now, and say why in prose. Anteroom remembers an explicit blank answer "
  + "as reviewed and stops asking for that optional field again. "
  + "If an optional field truly does not apply to the item, also set \"status\": \"not_applicable\" "
  + "on that row so Anteroom remembers it and excludes it from Ask chat. Missing evidence or no "
  + "suitable dropdown option alone does not mean a field is not applicable.";

const GENERAL_FIELD_ALIASES: Record<string, string> = {
  condition: "condition",
  primarycolor: "primaryColor",
  secondarycolor: "secondaryColor",
  size: "usSize",
};

/** Validation field names that do not match the scraped dropdown key. */
const FIELD_ALIASES: Record<string, string> = {
  whatis: "whatIsIt",
  whatisit: "whatIsIt",
  whomade: "whoMade",
  whenmade: "whenMade",
  parcelsize: "parcelSize",
  shippinglabel: "shippingLabel",
};

function optionKey(text: string): string {
  return String(text || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function leafKey(fieldKey: string): string {
  const parts = fieldKey.split(".");
  return parts[parts.length - 1] || fieldKey;
}

function optionsFor(
  forms: DropdownForms | undefined,
  marketplace: string,
  fieldLeaf: string,
): string[] | undefined {
  // This static scrape captured one men's category. Department options vary
  // by category; live schema options are supplied directly on editor fields.
  if (marketplace === "ebay" && optionKey(fieldLeaf) === "department") return undefined;
  const bucket = forms?.[marketplace];
  if (!bucket) return undefined;
  if (bucket[fieldLeaf]?.length) return bucket[fieldLeaf];
  const folded = optionKey(FIELD_ALIASES[optionKey(fieldLeaf)] || fieldLeaf);
  for (const [key, values] of Object.entries(bucket)) {
    if (optionKey(key) === folded && values.length) return values;
  }
  return undefined;
}

/**
 * Dropdown list for one validation field path ("depop_specifics.material").
 *
 * Fix-errors prompts paste these into chat so the model repairs a rejected
 * value with one the Depop/Etsy dropdown really offers instead of "Other".
 */
export function optionsForField(
  forms: DropdownForms | undefined,
  marketplace: string,
  field: string,
): string[] | undefined {
  if (!forms) return undefined;
  const leaf = leafKey(String(field || ""));
  if (!leaf) return undefined;
  const market = String(marketplace || "").toLowerCase();
  if (market === "general" || market === "vendoo") {
    const alias = GENERAL_FIELD_ALIASES[leaf.toLowerCase()];
    return optionsFor(forms, "vendoo", alias || leaf) || optionsFor(forms, "vendoo", leaf);
  }
  return optionsFor(forms, market, leaf);
}

/** Attach static dropdown options to editor fields that do not already have any. */
export function withDropdownOptions<T extends { key: string; options?: string[]; optionsSource?: string }>(
  fields: T[],
  tab: string,
  forms: DropdownForms | undefined,
): T[] {
  if (!forms) return fields;
  return fields.map((field) => {
    const leafName = leafKey(field.key).toLowerCase();
    // A live size list that came back empty stays empty. Other empty marketplace
    // lists fall through to the reference choices so the menu is not blank.
    if (field.options?.length || (field.optionsSource === "marketplace" && leafName === "size")) return field;
    const leaf = leafKey(field.key);
    let options: string[] | undefined;
    if (tab === "general") {
      const alias = GENERAL_FIELD_ALIASES[leaf.toLowerCase()];
      options = alias ? optionsFor(forms, "vendoo", alias) : undefined;
      if (!options) options = optionsFor(forms, "vendoo", leaf);
    } else {
      options = optionsFor(forms, tab, leaf);
    }
    return options?.length ? { ...field, options, optionsSource: "reference" } : field;
  });
}
