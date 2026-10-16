import type { FillLogEntry, FillLogReport, MarketplaceForm } from "../../api/types";
import { normalizeFieldName } from "./fieldKeys";
import {
  SKIP_KEYS,
  displayValue,
  fieldDisplayValue,
  fieldIsMissing,
  fieldLabel,
  isEmptyValue,
  notApplicableField,
} from "./fieldValues";
import { FILLABLE_STATUSES, leftoverEntries } from "./fillStatus";
import { organizeFields } from "./formLayout";
import { toForm, type DraftField, type DraftForm } from "./formModel";
import { MARKETPLACE_ORDER, liveStatusForMarketplace } from "./marketplaces";

/** Scraped on-page labels win over the key, which for Etsy is a bare taxonomy id. */
type FieldLabels = Record<string, string> | undefined;

/**
 * The Vendoo API keeps the category prefix (554_148789511893) while the DOM
 * scrape strips it (148789511893), and both can survive the merge, so try the
 * stripped form too.
 */
function strippedFieldKey(key: string): string {
  return key.replace(/^[0-9a-f]{8,}_/i, "").replace(/^\d+_/, "");
}

function labelFor(key: string, path: string, labels: FieldLabels): string {
  const scraped = labels && (labels[path] || labels[key] || labels[strippedFieldKey(key)]);
  if (typeof scraped === "string" && scraped.trim()) return scraped.trim();
  const parentPath = path.split(".").slice(0, -1).join(".");
  const parentKey = parentPath.split(".").pop() || "";
  const parent = parentKey && !/[A-Za-z]/.test(parentKey) && labels
    ? labels[parentPath] || labels[parentKey] || labels[strippedFieldKey(parentKey)]
    : undefined;
  return (typeof parent === "string" && parent.trim()) || fieldLabel(key);
}

function flattenFields(value: unknown, prefix = "", labels: FieldLabels = undefined): DraftField[] {
  if (value == null || typeof value !== "object" || Array.isArray(value)) {
    if (!prefix) return [];
    const label = labelFor(prefix.split(".").pop() || prefix, prefix, labels);
    const shown = fieldDisplayValue(value, label);
    return [{ key: prefix, label, value: shown, missing: fieldIsMissing(value, label) }];
  }
  const record = value as Record<string, unknown>;
  if (
    typeof record.displayName === "string" ||
    Array.isArray(record.displayPath) ||
    record.option ||
    (record.value != null && Object.keys(record).length <= 3)
  ) {
    const label = labelFor(prefix.split(".").pop() || prefix || "Value", prefix, labels);
    const shown = fieldDisplayValue(record, label);
    return [{
      key: prefix || "value",
      label,
      value: shown,
      missing: fieldIsMissing(record, label),
    }];
  }
  const rows: DraftField[] = [];
  for (const [key, nested] of Object.entries(record)) {
    if (SKIP_KEYS.has(key.toLowerCase())) continue;
    if (key === "category" && record.categoryV2) continue;
    const path = prefix ? `${prefix}.${key}` : key;
    if (nested && typeof nested === "object" && !Array.isArray(nested)) {
      const nestedRecord = nested as Record<string, unknown>;
      if ("option" in nestedRecord && "scale" in nestedRecord) {
        rows.push({
          key: `${path}.option`,
          label: "US Size",
          value: fieldDisplayValue(nestedRecord.option, "US Size"),
          missing: fieldIsMissing(nestedRecord.option, "US Size"),
        });
        rows.push({
          key: `${path}.scale`,
          label: "Size Type",
          value: fieldDisplayValue(nestedRecord.scale, "Size Type"),
          missing: fieldIsMissing(nestedRecord.scale, "Size Type"),
        });
        continue;
      }
      const leaf =
        typeof nestedRecord.displayName === "string" ||
        Array.isArray(nestedRecord.displayPath) ||
        nestedRecord.option ||
        ("value" in nestedRecord && Object.keys(nestedRecord).length <= 3);
      if (!leaf && !isEmptyValue(nested) && Object.keys(nestedRecord).length > 1) {
        rows.push(...flattenFields(nested, path, labels));
        continue;
      }
    }
    const label = labelFor(key, path, labels);
    const shown = fieldDisplayValue(nested, label);
    rows.push({
      key: path,
      label,
      value: shown,
      missing: fieldIsMissing(nested, label),
    });
  }
  return rows;
}

function listingSection(listing: Record<string, unknown> | undefined): unknown {
  if (!listing || typeof listing !== "object") return listing;
  const rest = { ...listing };
  const overrides = rest.overrides;
  const specifics = rest.marketplaceSpecifics;
  const category = rest.categorySpecifics;
  delete rest.overrides;
  delete rest.marketplaceSpecifics;
  delete rest.categorySpecifics;
  delete rest.fieldLabels;
  delete rest.type;
  return {
    ...rest,
    ...(overrides && typeof overrides === "object" && !Array.isArray(overrides) ? overrides as object : {}),
    ...(specifics && typeof specifics === "object" && !Array.isArray(specifics) ? specifics as object : {}),
    ...(category && typeof category === "object" && !Array.isArray(category) ? category as object : {}),
  };
}

/**
 * The extension records on-page labels for keys the id can't name, under
 * `fieldLabels` as "<bucket>.<key>". listingSection flattens the buckets away,
 * so re-key them to the bare field key that flattenFields will see.
 *
 * Always attach scraped labels — even when a camelCase twin exists. Duplicate
 * taxonomy-id rows are dropped later by withoutTaxonomyIdNoise; keeping the raw
 * id visible was what made Etsy Fields look like random numbers.
 */
function schemaFieldLabels(forms: MarketplaceForm[] | undefined, marketplace: string): FieldLabels {
  const form = forms?.find((entry) => entry.marketplace === marketplace);
  if (!form) return undefined;
  const out: Record<string, string> = {};
  for (const field of form.fields) {
    const key = String(field.key || "").trim();
    const label = String(field.label || "").trim();
    if (!key || !/[A-Za-z]/.test(label)) continue;
    out[key] = label;
    out[strippedFieldKey(key)] = label;
  }
  return Object.keys(out).length ? out : undefined;
}

function listingFieldLabels(
  listing: Record<string, unknown> | undefined,
  schemaLabels: FieldLabels = undefined,
): FieldLabels {
  const raw = listing?.fieldLabels;
  const out: Record<string, string> = { ...schemaLabels };
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return Object.keys(out).length ? out : undefined;
  }
  for (const [path, label] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof label !== "string" || !label.trim()) continue;
    const text = label.trim();
    out[path] = text;
    const bare = path.split(".").slice(1).join(".");
    if (bare) out[bare] = text;
  }
  return Object.keys(out).length ? out : undefined;
}

/** Etsy category specifics use bare taxonomy ids (148789511893) as DOM keys. */
function isTaxonomyIdFieldKey(key: string): boolean {
  return key.split(".").some((part) => Boolean(part) && !/[A-Za-z]/.test(part));
}

/**
 * Hide taxonomy-id noise: unlabeled digit keys, and labeled digit keys that
 * duplicate a real named sibling (closure + 325502673988 both meaning Closure).
 */
export function withoutTaxonomyIdNoise(fields: DraftField[]): DraftField[] {
  const namedLabels = new Set(
    fields
      .filter((field) => !isTaxonomyIdFieldKey(field.key))
      .map((field) => normalizeFieldName(field.label))
      .filter(Boolean),
  );
  return fields.filter((field) => {
    if (!isTaxonomyIdFieldKey(field.key)) return true;
    const labelName = normalizeFieldName(field.label);
    if (!labelName || !/[a-z]/.test(labelName)) return false;
    if (namedLabels.has(labelName)) return false;
    return true;
  });
}

function deepMergeRecords(
  base: Record<string, unknown> | undefined,
  overlay: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(overlay || {})) {
    const prev = out[key];
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      prev &&
      typeof prev === "object" &&
      !Array.isArray(prev)
    ) {
      out[key] = deepMergeRecords(prev as Record<string, unknown>, value as Record<string, unknown>);
      continue;
    }
    // Prefer a real value over an empty scrape/API placeholder so Refresh does
    // not wipe fields that are already present on either side.
    if (isEmptyValue(value) && !isEmptyValue(prev)) continue;
    out[key] = value;
  }
  return out;
}

export function mergeDraftItem(draft: {
  item?: unknown;
  form?: unknown;
  statuses?: unknown;
} | undefined): Record<string, unknown> | undefined {
  if (!draft) return undefined;
  const item = draft.item && typeof draft.item === "object" && !Array.isArray(draft.item)
    ? draft.item as Record<string, unknown>
    : {};
  const form = draft.form && typeof draft.form === "object" && !Array.isArray(draft.form)
    ? draft.form as Record<string, unknown>
    : {};
  if (!Object.keys(item).length && !Object.keys(form).length) return undefined;
  const formStatuses = form.statuses && typeof form.statuses === "object" && !Array.isArray(form.statuses)
    ? form.statuses as Record<string, unknown>
    : undefined;
  const draftStatuses = draft.statuses && typeof draft.statuses === "object" && !Array.isArray(draft.statuses)
    ? draft.statuses as Record<string, unknown>
    : undefined;
  // Deep-merge so empty optional categorySpecifics scraped from the page are
  // kept when the API omits those keys.
  return {
    ...form,
    ...item,
    generalDetails: deepMergeRecords(
      (form.generalDetails as Record<string, unknown> | undefined),
      (item.generalDetails as Record<string, unknown> | undefined),
    ),
    listings: deepMergeRecords(
      (form.listings as Record<string, unknown> | undefined),
      (item.listings as Record<string, unknown> | undefined),
    ),
    images: item.images ?? form.images,
    statuses: draftStatuses || formStatuses,
  };
}

function fillLogEntriesForMarket(report: FillLogReport | undefined, marketplace: string): FillLogEntry[] {
  return report?.by_marketplace[marketplace]?.entries || [];
}

export function formsFromDraft(
  item: Record<string, unknown> | null | undefined,
  report?: FillLogReport,
  schemaForms?: MarketplaceForm[],
): DraftForm[] {
  const leftovers = report ? leftoverEntries(report) : [];
  const forms: DraftForm[] = [];
  const general = item?.generalDetails && typeof item.generalDetails === "object"
    ? flattenFields(item.generalDetails)
    : [];
  if (item?.images && Array.isArray(item.images) && !general.some((field) => field.key === "images")) {
    general.unshift({
      key: "images",
      label: "Photos",
      value: displayValue(item.images),
      missing: item.images.length === 0,
    });
  } else if ((item?.generalDetails as Record<string, unknown> | undefined)?.images) {
    const images = (item?.generalDetails as Record<string, unknown> | undefined)?.images;
    if (!general.some((field) => field.key === "images" || field.label === "Photos")) {
      general.unshift({
        key: "images",
        label: "Photos",
        value: displayValue(images),
        missing: isEmptyValue(images),
      });
    }
  }
  forms.push(toForm(
    "general",
    organizeFields("general", general, fillLogEntriesForMarket(report, "general")),
    liveStatusForMarketplace("general", item),
  ));

  const listings = item?.listings && typeof item.listings === "object"
    ? item.listings as Record<string, unknown>
    : {};
  const leftoverMarkets = new Set(leftovers.map((entry) => entry.marketplace.toLowerCase()));
  const scrapedStatuses = item?.statuses && typeof item.statuses === "object" && !Array.isArray(item.statuses)
    ? item.statuses as Record<string, unknown>
    : {};
  // Always include the standard marketplaces so empty Category optionals still
  // appear even when the Vendoo API omits an empty listings.ebay object.
  // Also keep any marketplace that has a live Vendoo status (Facebook, etc.).
  const listingIds = [
    ...MARKETPLACE_ORDER.filter((id) => id !== "general"),
    ...Object.keys(scrapedStatuses).filter((id) => id !== "general" && !MARKETPLACE_ORDER.includes(id)),
    ...Object.keys(listings).filter((id) => {
      if (MARKETPLACE_ORDER.includes(id) || id === "validate" || id in scrapedStatuses) return false;
      const listing = listings[id] as Record<string, unknown> | undefined;
      const status = listing?.status as Record<string, unknown> | undefined;
      return Boolean(status?.listed) || leftoverMarkets.has(id);
    }),
  ];
  for (const id of listingIds) {
    const listing = listings[id] as Record<string, unknown> | undefined;
    const section = listing ? listingSection(listing) : undefined;
    const raw = listing
      ? withoutTaxonomyIdNoise(flattenFields(
        section,
        "",
        listingFieldLabels(listing, schemaFieldLabels(schemaForms, id)),
      ))
      : [];
    const fields = organizeFields(id, raw, fillLogEntriesForMarket(report, id));
    const liveStatus = liveStatusForMarketplace(id, item);
    if (!fields.length && !liveStatus) continue;
    forms.push(toForm(id, fields, liveStatus));
  }
  return forms;
}

export function formsFromFillLog(report: FillLogReport): DraftForm[] {
  const ids = [
    ...MARKETPLACE_ORDER.filter((id) => report.by_marketplace[id]),
    ...Object.keys(report.by_marketplace).filter((id) => !MARKETPLACE_ORDER.includes(id)),
  ];
  return ids.map((id) => {
    const group = report.by_marketplace[id];
    const fields: DraftField[] = group.entries.map((entry) => {
      if (entry.status === "not_applicable") {
        return notApplicableField({
          key: entry.id,
          label: entry.field,
          value: "",
          missing: false,
        });
      }
      const pending = FILLABLE_STATUSES.has(entry.status);
      return {
        key: entry.id,
        label: entry.field,
        // Pending leftovers keep their proposed value only in the leftover input.
        value: pending ? "" : entry.value_preview || "",
        missing: pending,
        leftover: pending ? entry : undefined,
      };
    });
    return toForm(id, organizeFields(id, fields, group.entries));
  });
}
