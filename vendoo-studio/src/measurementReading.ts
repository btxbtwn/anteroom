import type { MeasurementReadResult } from "./api/types";
import { garmentFields, type Garment } from "./components/garmentMeasurements";

/** Items read at once from a window's Read measurements. */
export const READ_CONCURRENCY = 2;

export interface ReadingOutcome {
  /** The row's measurements with read values filled into blank fields only. */
  measurements: Record<string, string>;
  /** Fields filled from the photos, shown as suggestions until the seller edits them. */
  suggested: string[];
  /** Plain-words status for the item. */
  note: string;
}

/**
 * Fold a photo reading into what the seller has typed. A typed value always wins;
 * only blank fields take an accepted reading, and every field the photos could
 * not settle is named so the seller knows to measure it by hand.
 */
export function applyReading(
  garment: Garment,
  typed: Record<string, string>,
  result: MeasurementReadResult,
): ReadingOutcome {
  if (!result.photos_read) {
    return { measurements: typed, suggested: [], note: "No yardstick photo found." };
  }
  const measurements = { ...typed };
  const suggested: string[] = [];
  const byHand: string[] = [];
  for (const field of garmentFields(garment)) {
    const reading = result.measurements[field.key];
    if (reading?.status === "accepted" && reading.value != null) {
      if (!(typed[field.key] || "").trim()) {
        measurements[field.key] = String(reading.value);
        suggested.push(field.key);
      }
    } else if (!(typed[field.key] || "").trim()) {
      byHand.push(field.label);
    }
  }
  const parts: string[] = [];
  if (suggested.length) parts.push("Filled from the photos. Check them.");
  else parts.push("Nothing new could be read from the photos.");
  if (byHand.length) parts.push(`Measure by hand: ${byHand.join(", ")}.`);
  return { measurements, suggested, note: parts.join(" ") };
}

/** Run `work` over every item, `limit` at a time; each reading is several photo-model calls. */
export async function readEach<T>(items: T[], limit: number, work: (item: T) => Promise<void>): Promise<void> {
  const queue = [...items];
  const next = async (): Promise<void> => {
    const item = queue.shift();
    if (item === undefined) return;
    await work(item);
    await next();
  };
  await Promise.all(Array.from({ length: limit }, next));
}

/**
 * A single listing's reading as a suggestion the seller accepts before it is saved:
 * what the photos read for blank fields, and what to measure by hand.
 */
export function readingSuggestion(garment: Garment, outcome: ReadingOutcome): string {
  const labels = new Map(garmentFields(garment).map((field) => [field.key, field.label]));
  const read = outcome.suggested.map((key) => `${labels.get(key)} ${outcome.measurements[key]} in`);
  const byHand = outcome.note.match(/Measure by hand: .*$/)?.[0];
  if (!read.length) return outcome.note;
  return [`From the photos: ${read.join(", ")}.`, byHand].filter(Boolean).join(" ");
}
