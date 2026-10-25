import type { FillLogEntry } from "../../api/types";
import {
  DEPOP_CATEGORY_OPTIONALS,
  EBAY_CATEGORY_CORE,
  EBAY_CATEGORY_OPTIONALS,
  ETSY_CATEGORY_OPTIONALS,
} from "../../marketplaceFields";
import { fieldMatchKey, normalizeFieldName } from "./fieldKeys";
import { fieldDisplayValue, isDoesNotApplyValue, notApplicableField } from "./fieldValues";
import { FILLABLE_STATUSES } from "./fillStatus";
import type { DraftField } from "./formModel";

type FieldSpec = { keys: string[]; label: string; always?: boolean };
type SectionSpec = { label: string; fields?: FieldSpec[]; extras?: boolean };

const SHARED_ITEM_FIELDS: FieldSpec[] = [
  { keys: ["title"], label: "Title" },
  { keys: ["description"], label: "Description" },
  { keys: ["brand"], label: "Brand" },
  { keys: ["condition"], label: "Condition" },
  { keys: ["primary color"], label: "Primary Color" },
  { keys: ["secondary color"], label: "Secondary Color" },
  { keys: ["quantity"], label: "Quantity" },
  { keys: ["sku"], label: "SKU" },
  { keys: ["price"], label: "Price" },
];

const PACKAGE_FIELDS: FieldSpec[] = [
  { keys: ["pounds"], label: "Package Weight (lb)" },
  { keys: ["ounces"], label: "Package Weight (oz)" },
  { keys: ["length"], label: "Length" },
  { keys: ["width"], label: "Width" },
  { keys: ["height"], label: "Height" },
];

/** Empty optional category fields Vendoo keeps off the API until filled. */
const EBAY_OPTIONAL_FIELDS: FieldSpec[] = EBAY_CATEGORY_OPTIONALS.map((field) => ({
  keys: [normalizeFieldName(field.label), normalizeFieldName(field.key)],
  label: field.label,
  always: true,
}));

const EBAY_CATEGORY_CORE_FIELDS: FieldSpec[] = [
  { keys: ["category"], label: "Category", always: true },
  ...EBAY_CATEGORY_CORE.map((field) => ({
    keys: [normalizeFieldName(field.label), normalizeFieldName(field.key)],
    label: field.label,
    always: true,
  })),
];

const ETSY_OPTIONAL_FIELDS: FieldSpec[] = ETSY_CATEGORY_OPTIONALS.map((field) => ({
  keys: [normalizeFieldName(field.label), normalizeFieldName(field.key), ...(field.key === "pattern" ? ["fabric pattern"] : [])],
  label: field.label,
  always: true,
}));

const DEPOP_OPTIONAL_FIELDS: FieldSpec[] = DEPOP_CATEGORY_OPTIONALS.map((field) => ({
  keys: [normalizeFieldName(field.label), normalizeFieldName(field.key)],
  label: field.label,
  always: true,
}));

const FORM_LAYOUTS: Record<string, SectionSpec[]> = {
  general: [
    { label: "Photos", fields: [{ keys: ["photos", "images"], label: "Photos", always: false }] },
    {
      label: "Item Details",
      fields: [
        { keys: ["title"], label: "Title", always: true },
        { keys: ["description"], label: "Description", always: true },
        { keys: ["brand"], label: "Brand", always: true },
        { keys: ["condition"], label: "Condition", always: true },
        { keys: ["primary color"], label: "Primary Color", always: true },
        { keys: ["secondary color"], label: "Secondary Color", always: true },
        { keys: ["sku"], label: "SKU", always: true },
        { keys: ["zip code"], label: "Inventory location", always: true },
        { keys: ["quantity"], label: "Quantity" },
      ],
    },
    {
      label: "Category",
      fields: [
        { keys: ["category"], label: "Category", always: true },
        { keys: ["size"], label: "US Size", always: true },
        { keys: ["size type"], label: "Size Type", always: true },
      ],
    },
    { label: "Package Details", fields: PACKAGE_FIELDS.map((field) => ({ ...field, always: true })) },
    {
      label: "Price",
      fields: [
        { keys: ["price"], label: "Listing Price", always: true },
        { keys: ["cost"], label: "Cost of Goods", always: true },
      ],
    },
    {
      label: "Additional Details",
      fields: [
        { keys: ["tags"], label: "Tags" },
        { keys: ["labels"], label: "Vendoo Labels", always: true },
        { keys: ["notes"], label: "Vendoo Internal Notes", always: true },
      ],
    },
    { label: "More fields", extras: true },
  ],
  ebay: [
    {
      label: "Item Details",
      fields: [
        { keys: ["title"], label: "Title" },
        { keys: ["description"], label: "Description" },
        { keys: ["brand"], label: "Brand" },
        { keys: ["primary color"], label: "Primary Color" },
        { keys: ["secondary color"], label: "Secondary Color" },
        { keys: ["quantity"], label: "Quantity" },
        { keys: ["sku"], label: "SKU" },
        { keys: ["price"], label: "Price" },
      ],
    },
    {
      label: "Category",
      fields: [
        ...EBAY_CATEGORY_CORE_FIELDS,
        ...EBAY_OPTIONAL_FIELDS,
      ],
      extras: true,
    },
    { label: "Package Details", fields: PACKAGE_FIELDS },
    {
      label: "Shipping & returns",
      fields: [
        { keys: ["zip code"], label: "Inventory location", always: true },
        { keys: ["shipping"], label: "Shipping" },
        { keys: ["returns"], label: "Returns" },
      ],
    },
  ],
  etsy: [
    { label: "Item Details", fields: SHARED_ITEM_FIELDS },
    {
      label: "Listing info",
      fields: [
        { keys: ["who made"], label: "Who made it" },
        { keys: ["what is it"], label: "What is it" },
        { keys: ["when made"], label: "When was it made" },
      ],
    },
    {
      label: "Category",
      fields: [
        { keys: ["category"], label: "Category", always: true },
        { keys: ["size"], label: "Size", always: true },
        ...ETSY_OPTIONAL_FIELDS,
      ],
      extras: true,
    },
    {
      label: "Tags & materials",
      fields: [
        { keys: ["tags"], label: "Tags" },
        { keys: ["materials"], label: "Materials" },
      ],
    },
  ],
  poshmark: [
    { label: "Item Details", fields: SHARED_ITEM_FIELDS },
    { label: "Category", fields: [{ keys: ["category"], label: "Category" }, { keys: ["size"], label: "Size" }] },
    {
      label: "Additional Details",
      fields: [
        { keys: ["style tags"], label: "Style Tags" },
        { keys: ["original price"], label: "Original Price" },
      ],
    },
    { label: "Item specifics", extras: true },
  ],
  mercari: [
    { label: "Item Details", fields: SHARED_ITEM_FIELDS },
    { label: "Category", fields: [{ keys: ["category"], label: "Category" }, { keys: ["size"], label: "Size" }] },
    { label: "Shipping", fields: [{ keys: ["shipping label"], label: "Shipping Label" }] },
    { label: "Item specifics", extras: true },
  ],
  depop: [
    { label: "Item Details", fields: SHARED_ITEM_FIELDS },
    { label: "Category", fields: [{ keys: ["category"], label: "Category" }, { keys: ["size"], label: "Size" }] },
    {
      label: "Optional fields",
      fields: DEPOP_OPTIONAL_FIELDS,
    },
    { label: "Item specifics", extras: true },
  ],
};

const DEFAULT_FORM_LAYOUT: SectionSpec[] = [
  { label: "Item Details", fields: SHARED_ITEM_FIELDS },
  { label: "Category", fields: [{ keys: ["category"], label: "Category" }, { keys: ["size"], label: "Size" }] },
  { label: "Package Details", fields: PACKAGE_FIELDS },
  { label: "Item specifics", extras: true },
];

export function organizeFields(marketplace: string, fields: DraftField[], leftovers: FillLogEntry[] = []): DraftField[] {
  const layout = FORM_LAYOUTS[marketplace] || DEFAULT_FORM_LAYOUT;
  const unused = new Map<string, DraftField[]>();
  for (const field of fields) {
    const key = fieldMatchKey(field);
    if (!key) continue;
    const bucket = unused.get(key) || [];
    bucket.push(field);
    unused.set(key, bucket);
  }
  const leftoverPool = leftovers.slice();
  const takeField = (keys: string[]): DraftField | undefined => {
    for (const key of keys) {
      const bucket = unused.get(key);
      if (bucket?.length) return bucket.shift();
    }
    return undefined;
  };
  const takeLeftover = (keys: string[]): FillLogEntry | undefined => {
    const wants = new Set(keys);
    const index = leftoverPool.findIndex((entry) => wants.has(normalizeFieldName(entry.field)));
    if (index < 0) return undefined;
    return leftoverPool.splice(index, 1)[0];
  };
  const attachLeftover = (field: DraftField | undefined): FillLogEntry | undefined => {
    if (field?.leftover && leftoverPool.includes(field.leftover)) {
      leftoverPool.splice(leftoverPool.indexOf(field.leftover), 1);
      return field.leftover;
    }
    return takeLeftover(field ? [fieldMatchKey(field)] : []);
  };
  const placed = new Map<string, DraftField[]>();
  let extrasSection = "Item specifics";

  for (const section of layout) {
    const rows: DraftField[] = placed.get(section.label) || [];
    if (section.extras) extrasSection = section.label;
    for (const spec of section.fields || []) {
      const found = takeField(spec.keys);
      const leftover = found
        ? attachLeftover(found) || takeLeftover([...spec.keys, normalizeFieldName(spec.label)])
        : takeLeftover([...spec.keys, normalizeFieldName(spec.label)]);
      if (found) {
        // Only the live Vendoo value counts as filled. Leftover previews are
        // proposed fill values and must stay in the red/missing column.
        if (found.notApplicable || isDoesNotApplyValue(found.value) || leftover?.status === "not_applicable") {
          rows.push(notApplicableField({
            ...found,
            label: spec.label,
            section: section.label,
            value: "",
            missing: false,
          }));
          continue;
        }
        const value = fieldDisplayValue(found.value, spec.label);
        const pendingLeftover =
          value
            ? undefined
            : leftover && FILLABLE_STATUSES.has(leftover.status)
              ? leftover
              : found.leftover;
        rows.push({
          ...found,
          label: spec.label,
          section: section.label,
          leftover: pendingLeftover && FILLABLE_STATUSES.has(pendingLeftover.status) ? pendingLeftover : undefined,
          missing: !value,
          notApplicable: false,
          value,
        });
        continue;
      }
      if (leftover?.status === "not_applicable") {
        rows.push(notApplicableField({
          key: leftover.id || `${marketplace}.${spec.keys[0]}`,
          label: spec.label,
          value: "",
          missing: false,
          section: section.label,
        }));
        continue;
      }
      if (spec.always || leftover) {
        const pendingLeftover = leftover && FILLABLE_STATUSES.has(leftover.status) ? leftover : undefined;
        rows.push({
          key: leftover?.id || `${marketplace}.${spec.keys[0]}`,
          label: spec.label,
          value: "",
          missing: true,
          leftover: pendingLeftover,
          section: section.label,
        });
      }
    }
    placed.set(section.label, rows);
  }

  const namedExtras = placed.get(extrasSection) || [];
  const extraRows: DraftField[] = [...unused.values()].flat().map((field) => {
    const leftover = attachLeftover(field);
    if (field.notApplicable || isDoesNotApplyValue(field.value) || leftover?.status === "not_applicable") {
      return notApplicableField({
        ...field,
        section: extrasSection,
        value: "",
        missing: false,
      });
    }
    const value = fieldDisplayValue(field.value, field.label);
    const pendingLeftover =
      value
        ? undefined
        : leftover && FILLABLE_STATUSES.has(leftover.status)
          ? leftover
          : field.leftover;
    return {
      ...field,
      leftover: pendingLeftover && FILLABLE_STATUSES.has(pendingLeftover.status) ? pendingLeftover : undefined,
      missing: !value,
      notApplicable: false,
      value,
      section: extrasSection,
    };
  });
  const seenExtras = new Set([...namedExtras, ...extraRows].map((field) => fieldMatchKey(field)));
  extraRows.push(...leftoverPool.flatMap((entry) => {
    const key = normalizeFieldName(entry.field);
    if (key && seenExtras.has(key)) return [];
    if (key) seenExtras.add(key);
    const label = entry.field.replace(/^(ebay|etsy|poshmark|mercari|depop|grailed|vinted|facebook|sellwild|vendoo)\s+/i, "").trim() || entry.field;
    if (entry.status === "not_applicable") {
      return [notApplicableField({
        key: entry.id,
        label,
        value: "",
        missing: false,
        section: extrasSection,
      })];
    }
    const pending = FILLABLE_STATUSES.has(entry.status);
    return [{
      key: entry.id,
      label,
      value: "",
      missing: pending || !fieldDisplayValue(entry.value_preview || "", label),
      leftover: pending ? entry : undefined,
      section: extrasSection,
    }];
  }));
  extraRows.sort((left, right) => left.label.localeCompare(right.label));
  placed.set(extrasSection, [...namedExtras, ...extraRows]);

  const seenKeys = new Set<string>();
  return layout.flatMap((section) => placed.get(section.label) || []).filter((field) => {
    const key = `${field.section}|${fieldMatchKey(field)}|${field.key}`;
    if (seenKeys.has(key)) return false;
    seenKeys.add(key);
    return true;
  });
}

export function groupFields(fields: DraftField[]): { label: string; fields: DraftField[] }[] {
  const groups: { label: string; fields: DraftField[] }[] = [];
  for (const field of fields) {
    const label = field.section || "";
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.fields.push(field);
    else groups.push({ label, fields: [field] });
  }
  return groups;
}
