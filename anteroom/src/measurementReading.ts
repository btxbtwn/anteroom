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
 * Hosted uploads share one per-seller upload slot (`UploadSlots` waits up to
 * 10 seconds before returning 429), and each read releases that slot before
 * slow vision inference starts. Keeping two items in flight overlaps one
 * item's upload with another's inference while keeping bursts small enough
 * for upload-slot retry to absorb contention.
 */
export const HOSTED_UPLOAD_READ_CONCURRENCY = 2;

/**
 * Run `work` over every item, either all at once (local desktop) or with at
 * most `concurrency` in flight at a time (hosted, so bulk reads do not pile
 * every folder into the per-seller upload slot simultaneously). When `signal`
 * aborts, no further queued items are started.
 */
export async function readEach<T>(
  items: T[],
  work: (item: T) => Promise<void>,
  concurrency?: number,
  signal?: AbortSignal,
): Promise<void> {
  if (!items.length || signal?.aborted) return;
  const limit = concurrency && concurrency > 0 ? Math.min(Math.floor(concurrency), items.length) : items.length;
  if (limit >= items.length && !signal) {
    await Promise.all(items.map(work));
    return;
  }
  let next = 0;
  const worker = async () => {
    while (next < items.length && !signal?.aborted) {
      const index = next++;
      await work(items[index]!);
    }
  };
  await Promise.all(Array.from({ length: limit }, worker));
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
