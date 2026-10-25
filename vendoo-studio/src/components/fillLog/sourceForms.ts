import type { FillLogReport, MarketplaceForm } from "../../api/types";
import { formsFromDraft, formsFromFillLog, withoutTaxonomyIdNoise } from "./draftForms";
import { fieldMatchKey } from "./fieldKeys";
import {
  fieldDisplayValue,
  fieldIsMissing,
  fieldLabel,
  isDoesNotApplyValue,
  notApplicableField,
} from "./fieldValues";
import { organizeFields } from "./formLayout";
import { toForm, type DraftField, type DraftForm } from "./formModel";
import { allowedMarketplaceIds, isLiveOnMarketplace } from "./marketplaces";

function overlayListingForms(forms: DraftForm[], listingForms: DraftForm[]): DraftForm[] {
  if (!listingForms.length) return forms;
  const byId = new Map(listingForms.map((form) => [form.id, form]));
  const seenForms = new Set(forms.map((form) => form.id));
  const merged = forms.map((form) => {
    const listingForm = byId.get(form.id);
    if (!listingForm) return form;
    const listingByKey = new Map(
      listingForm.fields.map((field) => [fieldMatchKey(field), field]),
    );
    // Keep Vendoo draft emptiness authoritative. Listing/chat values are only
    // candidates for Fill — never paint them as already filled on Vendoo.
    // Does-not-apply listing values become yellow N/A rows instead of red missing.
    const fields = form.fields.map((field) => {
      const listingFieldRow = listingByKey.get(fieldMatchKey(field));
      if (listingFieldRow?.notApplicable || isDoesNotApplyValue(listingFieldRow?.value)) {
        return notApplicableField({ ...field, section: field.section || listingFieldRow?.section });
      }
      if (field.notApplicable || isDoesNotApplyValue(field.value)) {
        return notApplicableField(field);
      }
      return field;
    });
    const seen = new Set(fields.map((field) => fieldMatchKey(field)).filter(Boolean));
    const extrasSection = form.id === "ebay" || form.id === "etsy" ? "Category" : "Item specifics";
    const extras: DraftField[] = [];
    for (const field of listingForm.fields) {
      const key = fieldMatchKey(field);
      if (!key || seen.has(key)) continue;
      if (field.notApplicable || isDoesNotApplyValue(field.value)) {
        seen.add(key);
        extras.push(notApplicableField({
          ...field,
          section: extrasSection,
        }));
        continue;
      }
      if (fieldIsMissing(field.value, field.label)) continue;
      seen.add(key);
      extras.push({
        ...field,
        value: "",
        section: extrasSection,
        missing: true,
        listingOnly: true,
      });
    }
    if (!extras.length) return toForm(form.id, fields, form.liveStatus);
    const next = [...fields];
    let insertAt = next.length;
    for (let i = 0; i < next.length; i += 1) {
      if (next[i].section === extrasSection) insertAt = i + 1;
    }
    next.splice(insertAt, 0, ...extras);
    return toForm(form.id, next, form.liveStatus);
  });
  for (const listingForm of listingForms) {
    if (seenForms.has(listingForm.id) || !listingForm.fields.length) continue;
    merged.push(listingForm);
  }
  return merged;
}

export function sourceFormsForJob(
  item: Record<string, unknown> | undefined,
  report: FillLogReport | undefined,
  listing: Record<string, unknown> | undefined,
  selectedMarketplaces?: string[],
  schemaForms?: MarketplaceForm[],
): { sourceForms: DraftForm[]; fromVendooDraft: boolean } {
  const enabled = allowedMarketplaceIds(selectedMarketplaces);
  const draftForms = item ? formsFromDraft(item, report, schemaForms) : [];
  const fillForms = report && Object.keys(report.by_marketplace).length ? formsFromFillLog(report) : [];
  const listingForms = formsFromListing(listing);
  const sourceForms = overlayListingForms(
    draftForms.length ? draftForms : fillForms.length ? fillForms : listingForms,
    listingForms,
  ).filter((form) => enabled.has(form.id) || isLiveOnMarketplace(form.liveStatus));
  return { sourceForms, fromVendooDraft: draftForms.length > 0 };
}

const LISTING_GENERAL_FIELDS: { key: string; label: string }[] = [
  { key: "title", label: "Title" },
  { key: "description", label: "Description" },
  { key: "brand", label: "Brand" },
  { key: "condition", label: "Condition" },
  { key: "primaryColor", label: "Primary Color" },
  { key: "secondaryColor", label: "Secondary Color" },
  { key: "sku", label: "SKU" },
  { key: "zipCode", label: "Inventory location" },
  { key: "quantity", label: "Quantity" },
  { key: "category_path", label: "Category" },
  { key: "size", label: "US Size" },
  { key: "sizeType", label: "Size Type" },
  { key: "weight_lb", label: "Package Weight (lb)" },
  { key: "weight_oz", label: "Package Weight (oz)" },
  { key: "price", label: "Listing Price" },
  { key: "cost", label: "Cost of Goods" },
  { key: "tags", label: "Tags" },
  { key: "labels", label: "Vendoo Labels" },
  { key: "notes", label: "Vendoo Internal Notes" },
];

function nestedListingValue(listing: Record<string, unknown>, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => {
    if (!current || typeof current !== "object" || Array.isArray(current)) return undefined;
    return (current as Record<string, unknown>)[key];
  }, listing);
}

function listingField(listing: Record<string, unknown>, key: string, label: string): DraftField {
  const raw = nestedListingValue(listing, key);
  if (isDoesNotApplyValue(raw)) {
    return notApplicableField({ key, label, value: "", missing: false });
  }
  return { key, label, value: fieldDisplayValue(raw, label), missing: fieldIsMissing(raw, label) };
}

function specificsListingFields(listing: Record<string, unknown>, marketplace: string): DraftField[] {
  const specs = listing[`${marketplace}_specifics`];
  if (!specs || typeof specs !== "object" || Array.isArray(specs)) return [];
  const fields: DraftField[] = [];
  for (const [key, value] of Object.entries(specs as Record<string, unknown>)) {
    if (key === "category_specifics" && value && typeof value === "object" && !Array.isArray(value)) {
      for (const [nested, nestedValue] of Object.entries(value as Record<string, unknown>)) {
        const label = fieldLabel(nested);
        if (isDoesNotApplyValue(nestedValue)) {
          fields.push(notApplicableField({
            key: `${marketplace}_specifics.category_specifics.${nested}`,
            label,
            value: "",
            missing: false,
          }));
          continue;
        }
        fields.push({
          key: `${marketplace}_specifics.category_specifics.${nested}`,
          label,
          value: fieldDisplayValue(nestedValue, label),
          missing: fieldIsMissing(nestedValue, label),
        });
      }
      continue;
    }
    const label = fieldLabel(key);
    if (isDoesNotApplyValue(value)) {
      fields.push(notApplicableField({
        key: `${marketplace}_specifics.${key}`,
        label,
        value: "",
        missing: false,
      }));
      continue;
    }
    fields.push({
      key: `${marketplace}_specifics.${key}`,
      label,
      value: fieldDisplayValue(value, label),
      missing: fieldIsMissing(value, label),
    });
  }
  return fields;
}

function formsFromListing(listing?: Record<string, unknown>): DraftForm[] {
  if (!listing || typeof listing !== "object") return [];
  const general = LISTING_GENERAL_FIELDS.map((field) => listingField(listing, field.key, field.label));
  const forms: DraftForm[] = [toForm("general", organizeFields("general", general))];
  for (const id of ["ebay", "etsy", "poshmark", "mercari", "depop", "grailed", "vinted", "facebook", "sellwild"]) {
    const extras: DraftField[] = [];
    if (id === "poshmark") extras.push(listingField(listing, "poshmark_specifics.originalPrice", "Original Price"));
    if (id === "mercari") extras.push(listingField(listing, "mercari_specifics.shippingLabel", "Shipping Label"));
    const seen = new Set(extras.map((field) => field.key));
    const fields = withoutTaxonomyIdNoise([
      ...extras,
      ...specificsListingFields(listing, id).filter((field) => !seen.has(field.key)),
    ]);
    const organized = organizeFields(id, fields);
    if (!organized.length) continue;
    forms.push(toForm(id, organized));
  }
  return forms;
}
