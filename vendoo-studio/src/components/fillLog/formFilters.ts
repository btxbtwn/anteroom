import {
  fieldMatchKey,
  isAccountManagedField,
  isSystemField,
  isUnfillableField,
  normalizeFieldName,
} from "./fieldKeys";
import { listingFieldEmpty, listingTextForField } from "./fieldValues";
import { toForm, type DraftField, type DraftForm } from "./formModel";

type HiddenField = { marketplace: string; field: string; label: string };
type HiddenFieldsState = { always: HiddenField[]; listing: HiddenField[] };
export type OpenMenu = { kind: "hidden" } | { kind: "field"; key: string } | null;

export function emptyHiddenFields(): HiddenFieldsState {
  return { always: [], listing: [] };
}

export function hiddenFieldKey(marketplace: string, field: string): string {
  return `${marketplace}:${field}`;
}

export function hiddenKeySet(hidden: HiddenFieldsState): Set<string> {
  return new Set(
    [...hidden.always, ...hidden.listing].map((item) => hiddenFieldKey(item.marketplace, item.field)),
  );
}

function isFieldHidden(hidden: Set<string>, marketplace: string, field: DraftField): boolean {
  if (hidden.has(hiddenFieldKey(marketplace, fieldMatchKey(field)))) return true;
  // Hidden entries are stored by label, so a field hidden back when it showed a
  // raw id would reappear once a scraped label names it. Only letterless keys
  // get this fallback -- matching real key names here would hide sibling fields
  // that happen to share a normalized name (layout key "size" vs label "Size").
  const rawKey = normalizeFieldName(field.key.split(".").pop() || field.key);
  if (!rawKey || /[a-z]/.test(rawKey)) return false;
  return hidden.has(hiddenFieldKey(marketplace, rawKey));
}

export function withoutHiddenFields(forms: DraftForm[], hidden: HiddenFieldsState): DraftForm[] {
  const keys = hiddenKeySet(hidden);
  return forms
    .map((form) => {
      const fields = form.fields.filter(
        (field) => !isFieldHidden(keys, form.id, field)
          && !isAccountManagedField(form.id, field)
          && !isSystemField(field),
      );
      return toForm(form.id, fields, form.liveStatus);
    })
    .filter((form) => form.fields.length > 0 || Boolean(form.liveStatus));
}

function fieldNeedsAttention(
  form: DraftForm,
  field: DraftField,
  listing: Record<string, unknown> | undefined,
): boolean {
  if (field.notApplicable || isUnfillableField(field, form.id)) return false;
  return listingFieldEmpty(listing, form.id, field);
}

export function filterForms(
  forms: DraftForm[],
  query: string,
  missingOnly: boolean,
  listing?: Record<string, unknown>,
): DraftForm[] {
  const needle = query.trim().toLowerCase();
  const filtered = missingOnly || Boolean(needle);
  return forms
    .map((form) => {
      const fields = form.fields.filter((field) => {
        if (missingOnly && !fieldNeedsAttention(form, field, listing)) return false;
        if (!needle) return true;
        const listingValue = listingTextForField(listing, form.id, field);
        return (
          form.label.toLowerCase().includes(needle) ||
          (form.liveStatus || "").toLowerCase().includes(needle) ||
          (field.section || "").toLowerCase().includes(needle) ||
          field.label.toLowerCase().includes(needle) ||
          field.value.toLowerCase().includes(needle) ||
          listingValue.toLowerCase().includes(needle)
        );
      });
      if (needle && !fields.length && !form.label.toLowerCase().includes(needle)
        && !(form.liveStatus || "").toLowerCase().includes(needle)) return null;
      const visible = filtered ? fields : form.fields;
      if (!visible.length && filtered) return null;
      return toForm(form.id, visible, form.liveStatus);
    })
    .filter((form): form is DraftForm => Boolean(form));
}
