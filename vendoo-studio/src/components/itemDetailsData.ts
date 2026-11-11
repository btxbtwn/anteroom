import { GARMENTS, garmentForListing, type Garment, type Measurements } from "./garmentMeasurements";

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

function knownGarment(value: unknown): Garment | null {
  if (value === "shorts") return "pants";
  return GARMENTS.find((g) => g.id === value)?.id ?? null;
}

/** The garment written in the notes, or null when none was. */
export function savedGarment(notes: string | null): Garment | null {
  try {
    const value: unknown = JSON.parse(notes || "null");
    return isStructuredNotes(value) ? knownGarment(value.garment) : null;
  } catch {
    return null;
  }
}

/**
 * Typed measurements keep the garment they were typed for. Before any are typed,
 * a saved garment may be only the default an earlier notes edit wrote, so the
 * listing's category (or title) picks it: jeans open on Pants, not Top.
 */
function chooseGarment(saved: Garment | null, measurements: Measurements, listing?: ListingHint): Garment {
  const typed = GARMENTS.filter((g) => Object.keys(measurements[g.id]).length).map((g) => g.id);
  if (typed.length) return saved && typed.includes(saved) ? saved : saved ?? typed[0]!;
  const guess = listing ? garmentForListing(listing) : DEFAULTS.garment;
  return guess !== DEFAULTS.garment ? guess : saved ?? DEFAULTS.garment;
}

/** The listing's category and title, which pick the garment until measurements are typed. */
export interface ListingHint { category?: string; title?: string }

export function parseNotes(notes: string | null, defaultPackageDimensions = DEFAULTS.packageDimensions, listing?: ListingHint): ItemDetailsData {
  if (!notes) {
    const measurements = parseMeasurements(null);
    return { ...DEFAULTS, packageDimensions: defaultPackageDimensions, measurements, garment: chooseGarment(null, measurements, listing) };
  }
  try {
    const value: unknown = JSON.parse(notes);
    const parsed = isStructuredNotes(value) ? value as Omit<Partial<ItemDetailsData>, "garment"> & { garment?: string } : { sellerNotes: notes };
    const measurements = parseMeasurements(parsed.measurements);
    return {
      sku: parsed.sku || "",
      sellerNotes: parsed.sellerNotes || "",
      knownFlaws: parsed.knownFlaws || "",
      descriptionMeasurements: parsed.descriptionMeasurements || "",
      cog: parsed.cog || "",
      packageDimensions: parsed.packageDimensions || defaultPackageDimensions,
      vendooLabels: parsed.vendooLabels ?? DEFAULTS.vendooLabels,
      garment: chooseGarment(knownGarment(parsed.garment), measurements, listing),
      measurements,
    };
  } catch {
    return { ...DEFAULTS, sellerNotes: notes, packageDimensions: defaultPackageDimensions, measurements: parseMeasurements(null),
      garment: chooseGarment(null, parseMeasurements(null), listing) };
  }
}

/**
 * The fields an edit changed. A garment picked from the category, not yet saved
 * (`storedGarment` differs), goes with the first edit, so generation reads the
 * measurements under the garment the seller saw.
 */
export function changedDetails(before: ItemDetailsData, after: ItemDetailsData, storedGarment?: Garment | null): Partial<ItemDetailsData> {
  const patch: Partial<ItemDetailsData> = Object.fromEntries(Object.entries(after).filter(([key, value]) =>
    key !== "measurements" && JSON.stringify(before[key as keyof ItemDetailsData]) !== JSON.stringify(value)));
  const measurements: Measurements = { top: {}, pants: {}, skirt: {} };
  for (const { id } of GARMENTS) {
    for (const key of new Set([...Object.keys(before.measurements[id]), ...Object.keys(after.measurements[id])])) {
      if (before.measurements[id][key] !== after.measurements[id][key]) measurements[id][key] = after.measurements[id][key] ?? "";
    }
  }
  if (Object.values(measurements).some(values => Object.keys(values).length)) patch.measurements = measurements;
  if (storedGarment !== undefined && Object.keys(patch).length && after.garment !== storedGarment) patch.garment = after.garment;
  return patch;
}

export function mergeMeasurements(before?: Measurements, patch?: Measurements): Measurements {
  return { top: { ...before?.top, ...patch?.top }, pants: { ...before?.pants, ...patch?.pants }, skirt: { ...before?.skirt, ...patch?.skirt } };
}

export function applyDetailsPatch(before: ItemDetailsData, patch: Partial<ItemDetailsData> | null): ItemDetailsData {
  return { ...before, ...patch, measurements: mergeMeasurements(before.measurements, patch?.measurements) };
}

/**
 * The notes keys an Item Details save sends: what the seller changed, and each
 * detail the notes do not hold yet as the editor shows it. The server merges
 * them into the notes as they are then, so keys it wrote since `notes` was
 * read (a category override, a delist record) are kept.
 */
export function detailNoteChanges(notes: string | null, patch: Partial<ItemDetailsData>, dimensions: string): Record<string, unknown> {
  let stored: Record<string, unknown> = {};
  try {
    const raw: unknown = JSON.parse(notes || "null");
    if (isStructuredNotes(raw)) stored = raw;
  } catch { /* Seller text becomes sellerNotes below. */ }
  const shown = parseNotes(notes, dimensions);
  const { measurements: typed, ...fields } = patch;
  const missing = Object.fromEntries(Object.entries(shown).filter(([key]) => key !== "measurements" && !(key in stored)));
  const storedMeasurements = (stored.measurements && typeof stored.measurements === "object" ? stored.measurements : {}) as Record<string, unknown>;
  // A garment the notes keep under an older shape (shorts) is written whole, so the merge does not hide it.
  const measurements = typed && Object.fromEntries(GARMENTS.filter(({ id }) => Object.keys(typed[id]).length).map(({ id }) =>
    [id, storedMeasurements[id] && typeof storedMeasurements[id] === "object" ? typed[id] : { ...shown.measurements[id], ...typed[id] }]));
  return { ...missing, ...fields, ...(measurements && Object.keys(measurements).length ? { measurements } : {}), condition: "", poshmarkOriginalPrice: "0" };
}
