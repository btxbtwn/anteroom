import type { MeasurementReadResult } from "./api/types";
import { garmentFields, type Garment } from "./components/garmentMeasurements";

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
  // Notes never name the provider or model: sellers paste them, and a model name
  // does not belong in a listing. The server logs which one read.
  const parts: string[] = [];
  if (suggested.length) parts.push("Filled from the photos. Check them.");
  else if (!Object.values(result.measurements).some((read) => read.status === "accepted")) {
    parts.push("The yardstick could not be read.");
  } else parts.push("Nothing new could be read from the photos.");
  if (byHand.length) parts.push(`Measure by hand: ${byHand.join(", ")}.`);
  return { measurements, suggested, note: parts.join(" ") };
}

/**
 * Run `work` over every item concurrently so model reads overlap instead of
 * waiting in waves. Hosted upload-body pacing happens in `client.ts` only for
 * the upload phase, because the server releases its upload slot before model
 * inference starts.
 */
export async function readEach<T>(
  items: T[],
  work: (item: T) => Promise<void>,
  signal?: AbortSignal,
): Promise<void> {
  if (!items.length || signal?.aborted) return;
  await Promise.all(items.map((item) => (signal?.aborted ? Promise.resolve() : work(item))));
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
