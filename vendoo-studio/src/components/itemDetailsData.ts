import { GARMENTS, type Garment, type Measurements } from "./garmentMeasurements";

export interface ItemDetailsData {
  sku: string;
  sellerNotes: string;
  knownFlaws: string;
  descriptionMeasurements: string;
  cog: string;
  packageDimensions: string;
  vendooLabels: string;
  garment: Garment;
  measurements: Measurements;
}

export const DEFAULTS: ItemDetailsData = {
  sku: "",
  sellerNotes: "",
  knownFlaws: "",
  descriptionMeasurements: "",
  cog: "",
  packageDimensions: "13x10x3",
  vendooLabels: "",
  garment: "top",
  measurements: { top: {}, pants: {}, skirt: {} },
};

function parseMeasurements(raw: unknown): Measurements {
  const source = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const out: Measurements = { top: {}, pants: {}, skirt: {} };
  for (const garment of GARMENTS) {
    // Listings saved before shorts folded into pants kept a separate shorts set.
    const values = source[garment.id] ?? (garment.id === "pants" ? source.shorts : undefined);
    if (!values || typeof values !== "object") continue;
    for (const field of garment.fields) {
      const value = (values as Record<string, unknown>)[field.key];
      if (typeof value === "string" && value) out[garment.id][field.key] = value;
    }
  }
  return out;
}

/** Keys the server and the editor write into a listing's notes besides the seller's own details. */
const MANAGED_NOTE_KEYS = new Set([
  "categoryOverride", "askingPrice", "titlePinned", "mergedInto", "condition", "poshmarkOriginalPrice",
  "vendooItemId", "vendooUrl", "vendooCoverUrl", "vendooFormUpdatedAt", "vendooUpdatedAt",
  "vendooRelistPending", "vendooMarketplaces", "vendooStatus",
]);

/** Notes saved as an object of Anteroom's keys, as opposed to seller text that happens to be JSON. */
function isStructuredNotes(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value as object).some((key) => key in DEFAULTS || MANAGED_NOTE_KEYS.has(key));
}

export function parseNotes(notes: string | null, defaultPackageDimensions = DEFAULTS.packageDimensions): ItemDetailsData {
  if (!notes) return { ...DEFAULTS, packageDimensions: defaultPackageDimensions, measurements: parseMeasurements(null) };
  try {
    const value: unknown = JSON.parse(notes);
    const parsed = isStructuredNotes(value) ? value as Omit<Partial<ItemDetailsData>, "garment"> & { garment?: string } : { sellerNotes: notes };
    return {
      sku: parsed.sku || "",
      sellerNotes: parsed.sellerNotes || "",
      knownFlaws: parsed.knownFlaws || "",
      descriptionMeasurements: parsed.descriptionMeasurements || "",
      cog: parsed.cog || "",
      packageDimensions: parsed.packageDimensions || defaultPackageDimensions,
      vendooLabels: parsed.vendooLabels ?? DEFAULTS.vendooLabels,
      garment: parsed.garment === "shorts"
        ? "pants"
        : GARMENTS.some((g) => g.id === parsed.garment) ? parsed.garment as Garment : DEFAULTS.garment,
      measurements: parseMeasurements(parsed.measurements),
    };
  } catch {
    return { ...DEFAULTS, sellerNotes: notes, packageDimensions: defaultPackageDimensions, measurements: parseMeasurements(null) };
  }
}

export function changedDetails(before: ItemDetailsData, after: ItemDetailsData): Partial<ItemDetailsData> {
  const patch: Partial<ItemDetailsData> = Object.fromEntries(Object.entries(after).filter(([key, value]) =>
    key !== "measurements" && JSON.stringify(before[key as keyof ItemDetailsData]) !== JSON.stringify(value)));
  const measurements: Measurements = { top: {}, pants: {}, skirt: {} };
  for (const { id } of GARMENTS) {
    for (const key of new Set([...Object.keys(before.measurements[id]), ...Object.keys(after.measurements[id])])) {
      if (before.measurements[id][key] !== after.measurements[id][key]) measurements[id][key] = after.measurements[id][key] ?? "";
    }
  }
  if (Object.values(measurements).some(values => Object.keys(values).length)) patch.measurements = measurements;
  return patch;
}

export function mergeMeasurements(before?: Measurements, patch?: Measurements): Measurements {
  return { top: { ...before?.top, ...patch?.top }, pants: { ...before?.pants, ...patch?.pants }, skirt: { ...before?.skirt, ...patch?.skirt } };
}

export function applyDetailsPatch(before: ItemDetailsData, patch: Partial<ItemDetailsData> | null): ItemDetailsData {
  return { ...before, ...patch, measurements: mergeMeasurements(before.measurements, patch?.measurements) };
}

export function mergeDetailNotes(notes: string | null, patch: Partial<ItemDetailsData>, dimensions: string): string {
  let managed: Record<string, unknown> = {};
  try {
    const raw: unknown = JSON.parse(notes || "null");
    if (isStructuredNotes(raw)) managed = raw;
  } catch { /* Seller text is retained by parseNotes. */ }
  return JSON.stringify({ ...managed, ...applyDetailsPatch(parseNotes(notes, dimensions), patch), condition: "", poshmarkOriginalPrice: "0" });
}

