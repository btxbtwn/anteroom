import type { FillLogEntry } from "../../api/types";
import { fieldMatchKey, isUnfillableField, normalizeFieldName, normalizeLookupKey } from "./fieldKeys";
import { isDoesNotApplyValue, listingFieldEmpty, listingValueForField } from "./fieldValues";
import type { DraftField, DraftForm } from "./formModel";

// Blank on purpose: the listing rules fill SKU only when the seller gives one
// and Secondary Color only when a second color shows. Chat cannot answer them.
const SELLER_ONLY_FIELDS = new Set(["general:sku", "general:secondary color"]);

function isSellerOnlyField(marketplace: string, field: DraftField): boolean {
  return SELLER_ONLY_FIELDS.has(`${marketplace}:${normalizeLookupKey(field.label || field.key)}`);
}

export function fieldsNeedingListingValues(
  forms: DraftForm[],
  listing: Record<string, unknown> | undefined,
  fromVendooDraft = false,
): { form: DraftForm; field: DraftField }[] {
  return forms.flatMap((form) =>
    form.fields
      .filter((field) => !field.notApplicable && !isUnfillableField(field, form.id))
      .filter((field) => !isSellerOnlyField(form.id, field))
      .filter((field) => !isDoesNotApplyValue(listingValueForField(listing, form.id, field, true)))
      .filter((field) => !fieldIntentionallyEmpty(listing, form.id, field))
      .filter((field) => listingFieldEmpty(listing, form.id, field))
      .filter((field) => {
        if (!fromVendooDraft) return true;
        // After Send: only blanks confirmed on the live Vendoo draft form.
        if (field.listingOnly) return false;
        return field.missing;
      })
      .map((field) => ({ form, field })),
  );
}

function fieldIntentionallyEmpty(
  listing: Record<string, unknown> | undefined,
  marketplace: string,
  field: DraftField,
): boolean {
  const rows = listing?.reviewed_empty_fields;
  if (!Array.isArray(rows) || !listingFieldEmpty(listing, marketplace, field)) return false;
  return rows.some((row) => row && typeof row === "object"
    && String(row.marketplace || "general").toLowerCase() === marketplace
    && normalizeLookupKey(String(row.field || "")) === normalizeLookupKey(field.label || field.key));
}

export function applicableChatFailures(
  forms: DraftForm[],
  listing: Record<string, unknown> | undefined,
  failures: FillLogEntry[],
): FillLogEntry[] {
  return failures.filter((entry) => {
    const marketplace = String(entry.marketplace || "").toLowerCase();
    const field = forms.find((form) => form.id === marketplace)?.fields.find(
      (candidate) => fieldMatchKey(candidate) === normalizeFieldName(entry.field),
    ) || { key: entry.field, label: entry.field, value: "", missing: true };
    return entry.status !== "not_applicable" && !field.notApplicable
      && !fieldIntentionallyEmpty(listing, marketplace, field)
      && !isDoesNotApplyValue(listingValueForField(listing, marketplace, field, true));
  });
}

/** Deduped empty listing values + Apply/Send failures for the Ask-chat button. */
export function askChatTargetCount(
  forms: DraftForm[],
  listing: Record<string, unknown> | undefined,
  failures: FillLogEntry[],
  fromVendooDraft = false,
): number {
  const seen = new Set<string>();
  let count = 0;
  for (const entry of applicableChatFailures(forms, listing, failures)) {
    const key = `${String(entry.marketplace || "").toLowerCase()}:${normalizeFieldName(entry.field)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    count += 1;
  }
  for (const { form, field } of fieldsNeedingListingValues(forms, listing, fromVendooDraft)) {
    const key = `${form.id}:${fieldMatchKey(field)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    count += 1;
  }
  return count;
}
