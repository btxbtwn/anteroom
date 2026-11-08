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
  const parts: string[] = [];
  const reader = result.reader;
  if (suggested.length) {
    parts.push(reader ? `Filled from the photos by ${reader}. Check them.` : "Filled from the photos. Check them.");
  } else if (reader && !Object.values(result.measurements).some((read) => read.status === "accepted")) {
    parts.push(`${reader} could not read the yardstick.`);
  } else parts.push("Nothing new could be read from the photos.");
  if (byHand.length) parts.push(`Measure by hand: ${byHand.join(", ")}.`);
  return { measurements, suggested, note: parts.join(" ") };
}

/**
 * Run `work` over every item at once. Each reading is minutes of photo-model calls,
 * so reading two at a time kept a box of items waiting most of half an hour; the
 * server limits the memory-heavy photo search itself.
 */
export async function readEach<T>(items: T[], work: (item: T) => Promise<void>): Promise<void> {
  await Promise.all(items.map(work));
}
