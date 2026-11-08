export type Garment = "top" | "pants" | "skirt";

export interface MeasurementField {
  key: string;
  label: string;
  placeholder: string;
}

// Keys and labels mirror GARMENT_MEASUREMENTS in server listing_generate.py.
export const GARMENTS: { id: Garment; label: string; fields: MeasurementField[] }[] = [
  {
    id: "top",
    label: "Top",
    fields: [
      { key: "pitToPit", label: "Pit to Pit", placeholder: "22.5" },
      { key: "length", label: "Length", placeholder: "27" },
      { key: "sleeve", label: "Sleeve", placeholder: "9" },
    ],
  },
  // Pants cover shorts too: same waist, rise, inseam and leg opening.
  {
    id: "pants",
    label: "Pants",
    fields: [
      { key: "waist", label: "Waist", placeholder: "16" },
      { key: "rise", label: "Rise", placeholder: "11" },
      { key: "inseam", label: "Inseam", placeholder: "30" },
      { key: "legOpening", label: "Leg Opening", placeholder: "8" },
    ],
  },
  {
    id: "skirt",
    label: "Skirt",
    fields: [
      { key: "waist", label: "Waist", placeholder: "14" },
      { key: "hip", label: "Hip", placeholder: "19" },
      { key: "length", label: "Length", placeholder: "22" },
    ],
  },
];

export type Measurements = Record<Garment, Record<string, string>>;

export function garmentFields(garment: Garment): MeasurementField[] {
  return (GARMENTS.find((g) => g.id === garment) || GARMENTS[0]!).fields;
}

const PANTS_WORDS = /\b(pants|jeans|shorts|trousers|slacks|chinos|cargos?|joggers|sweatpants|leggings|overalls)\b/i;

const SKIRT_WORDS = /\b(skirts?|skorts?)\b/i;

/** A folder called "Levi's 505 jeans" is measured as pants, "pleated skirt" as a skirt; anything else starts as a top. */
export function guessGarment(title: string): Garment {
  if (SKIRT_WORDS.test(title)) return "skirt";
  return PANTS_WORDS.test(title) ? "pants" : "top";
}

/** Only the chosen garment's typed values, with blanks dropped. */
export function typedMeasurements(garment: Garment, values: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of garmentFields(garment)) {
    const value = (values[field.key] || "").trim();
    if (value) out[field.key] = value;
  }
  return out;
}

/**
 * Item Details notes with the garment and its measurements set, the rest kept.
 * Generation reads these as seller facts and writes them into the description
 * word for word.
 */
export function notesWithMeasurements(
  notes: string | null | undefined,
  garment: Garment,
  values: Record<string, string>,
): string {
  let parsed: Record<string, unknown> = {};
  try {
    const raw = notes ? JSON.parse(notes) : {};
    if (raw && typeof raw === "object" && !Array.isArray(raw)) parsed = raw as Record<string, unknown>;
  } catch {
    parsed = notes ? { sellerNotes: notes } : {};
  }
  const existing = parsed.measurements && typeof parsed.measurements === "object"
    ? (parsed.measurements as Record<string, unknown>)
    : {};
  return JSON.stringify({
    ...parsed,
    garment,
    measurements: { ...existing, [garment]: typedMeasurements(garment, values) },
  });
}

export interface ItemDetails {
  garment: Garment;
  measurements: Record<string, string>;
  labels: string;
  cog: string;
  sku: string;
}

function parseNotesObject(notes: string | null | undefined): Record<string, unknown> {
  try {
    const raw = notes ? JSON.parse(notes) : {};
    if (raw && typeof raw === "object" && !Array.isArray(raw)) return raw as Record<string, unknown>;
  } catch {
    return notes ? { sellerNotes: notes } : {};
  }
  return {};
}

function text(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

const LABEL_KEY = (label: string) => label.toLowerCase().replace(/[^a-z]/g, "");

/**
 * Measurements written as text, such as `Pit to pit 20"; Length: 27"` carried
 * from a listing's description, matched to the garment's fields by label.
 */
function measurementsFromText(garment: Garment, value: unknown): Record<string, string> {
  const byLabel = new Map(garmentFields(garment).map((field) => [LABEL_KEY(field.label), field.key]));
  const out: Record<string, string> = {};
  for (const part of text(value).split(/[;\n]+/)) {
    const match = /^\s*([A-Za-z][A-Za-z /-]*?)\s*[:-]?\s*(\d+(?:\.\d+)?)/.exec(part);
    const key = match ? byLabel.get(LABEL_KEY(match[1]!)) : undefined;
    if (key && !out[key]) out[key] = match![2]!;
  }
  return out;
}

/** The Item Details a listing already holds, for the regenerate review. */
export function parseItemDetails(notes: string | null | undefined, fallbackGarment: Garment): ItemDetails {
  const parsed = parseNotesObject(notes);
  const garment = GARMENTS.find((g) => g.id === parsed.garment)?.id ?? fallbackGarment;
  const all = parsed.measurements && typeof parsed.measurements === "object"
    ? (parsed.measurements as Record<string, unknown>)
    : {};
  const mine = all[garment] && typeof all[garment] === "object" ? (all[garment] as Record<string, unknown>) : {};
  return {
    garment,
    // Typed measurements win; blank ones come from the listing's description.
    measurements: {
      ...measurementsFromText(garment, parsed.descriptionMeasurements),
      ...Object.fromEntries(Object.entries(mine).map(([key, value]) => [key, text(value)]).filter(([, value]) => value)),
    },
    labels: text(parsed.vendooLabels),
    cog: text(parsed.cog),
    sku: text(parsed.sku),
  };
}

/** Notes with the reviewed details set; every other note field is kept. */
export function notesWithItemDetails(notes: string | null | undefined, details: ItemDetails): string {
  const withMeasurements = JSON.parse(notesWithMeasurements(notes, details.garment, details.measurements));
  return JSON.stringify({
    ...withMeasurements,
    vendooLabels: details.labels.trim(),
    cog: details.cog.trim(),
    sku: details.sku.trim(),
    poshmarkOriginalPrice: "0",
  });
}
