import type { FillLogEntry } from "../../api/types";
import { fieldMatchKey, isUnfillableField, normalizeFieldName, normalizeLookupKey } from "./fieldKeys";
import {
  isDoesNotApplyValue,
  listingFieldEmpty,
  listingTextForField,
  listingValueForField,
} from "./fieldValues";
import type { DraftField, DraftForm } from "./formModel";

export interface FormSyncCounts {
  onVendoo: number;
  vendooEmpty: number;
  readyToApply: number;
  needsChat: number;
  notApplicable: number;
}

export function vendooTextForField(field: DraftField): string {
  if (field.notApplicable) return "Does not apply";
  return field.value || "";
}

function normalizeComparableFieldText(text: string): string {
  return String(text || "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
}

function normalizeLooseFieldText(text: string): string {
  return normalizeComparableFieldText(text)
    .replace(/[–—]/g, "-")
    .replace(/[*?]+/g, "")
    .replace(/[_/]+/g, " ")
    .replace(/-/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

/** Listing/apply value already matches what Vendoo shows — no write needed. */
function applyValueMatchesVendoo(applyValue: string, field: DraftField): boolean {
  const got = normalizeComparableFieldText(vendooTextForField(field));
  const want = normalizeComparableFieldText(applyValue);
  if (!got || !want) return false;
  if (got === want) return true;
  return normalizeLooseFieldText(got) === normalizeLooseFieldText(want);
}

export function fieldNeedsVendooApply(
  field: DraftField,
  applyValue: string,
  marketplace?: string,
): boolean {
  if (!applyValue || field.notApplicable || isUnfillableField(field, marketplace) || field.listingOnly) {
    return false;
  }
  if (field.missing) return true;
  return !applyValueMatchesVendoo(applyValue, field);
}

export function formSyncCounts(
  form: DraftForm,
  listing: Record<string, unknown> | undefined,
  fromVendooDraft: boolean,
): FormSyncCounts {
  const counts: FormSyncCounts = {
    onVendoo: 0,
    vendooEmpty: 0,
    readyToApply: 0,
    needsChat: 0,
    notApplicable: 0,
  };
  for (const field of form.fields) {
    if (field.notApplicable) {
      counts.notApplicable += 1;
      continue;
    }
    if (isUnfillableField(field, form.id)) continue;
    const listingValue = listingTextForField(listing, form.id, field);
    if (fromVendooDraft) {
      if (!field.missing) {
        if (listingValue && !applyValueMatchesVendoo(listingValue, field)) {
          counts.readyToApply += 1;
        } else {
          counts.onVendoo += 1;
        }
        continue;
      }
      counts.vendooEmpty += 1;
      if (listingValue) counts.readyToApply += 1;
      else counts.needsChat += 1;
      continue;
    }
    if (listingValue) counts.onVendoo += 1;
    else counts.vendooEmpty += 1;
  }
  return counts;
}

export function fieldsNeedingListingValues(
  forms: DraftForm[],
  listing: Record<string, unknown> | undefined,
  fromVendooDraft = false,
): { form: DraftForm; field: DraftField }[] {
  return forms.flatMap((form) =>
    form.fields
      .filter((field) => !field.notApplicable && !isUnfillableField(field, form.id))
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

export function patchableChangedFields(
  forms: DraftForm[],
  listing: Record<string, unknown> | undefined,
  values: Record<string, string> = {},
): { id?: string; marketplace: string; field: string; value: string }[] {
  return forms.flatMap((form) =>
    form.fields
      // Empty-on-Vendoo fields and mismatches only — never re-walk matching controls.
      .filter((field) => !field.listingOnly && !field.notApplicable && !isUnfillableField(field, form.id))
      .map((field) => {
        const leftover = field.leftover;
        const typed = leftover ? String(values[leftover.id] || "").trim() : "";
        const value = typed || listingValueForField(listing, form.id, field);
        if (!fieldNeedsVendooApply(field, value, form.id)) return null;
        return leftover
          ? { id: leftover.id, marketplace: form.id, field: leftover.field || field.label, value }
          : { marketplace: form.id, field: field.label, value };
      })
      .filter((item): item is { id?: string; marketplace: string; field: string; value: string } => Boolean(item)),
  );
}
