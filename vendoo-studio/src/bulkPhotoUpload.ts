import type { Photo, PhotoUploadResult } from "./api/types";
import type { PhotoFolderGroup } from "./photoDrop";
import { listingTitleForFolder } from "./photoDrop";
import { joinLabels, splitLabels } from "./components/itemLabels";
import { notesWithMeasurements, typedMeasurements, type Garment } from "./components/garmentMeasurements";

export interface BulkUploadDefaults {
  cog: string;
  labels: string;
  /** The wholesale box these items came out of. */
  boxId?: string;
}

/** Clockwise, as the rotate route takes it; -90 turns the photo left. */
export type PhotoRotation = 90 | -90 | 180;

/** Quarter turns clockwise, as the bulk upload window counts them, to what the server rotates by. */
export function rotationForTurns(turns: number): PhotoRotation | null {
  const quarter = ((turns % 4) + 4) % 4;
  if (quarter === 1) return 90;
  if (quarter === 2) return 180;
  if (quarter === 3) return -90;
  return null;
}

/** One folder's photos plus what the seller typed for that item alone. */
export interface BulkUploadItem extends PhotoFolderGroup {
  /** Photos turned in the bulk upload window; applied once each one is saved. */
  rotations?: Map<File, PhotoRotation>;
  sku?: string;
  garment?: Garment;
  measurements?: Record<string, string>;
}

interface BulkUploadApi {
  create: (body: { title: string; notes?: string; box_id?: string }) => Promise<{ id: string }>;
  uploadPhotos: (convId: string, files: File[]) => Promise<PhotoUploadResult>;
  rotatePhoto: (convId: string, photoId: string, degrees: PhotoRotation) => Promise<Photo>;
}

export interface BulkListingUploadResult {
  convId: string;
  folder: string | null;
  count: number;
  errors: string[];
}

/** Build the same Item Details note fields used by an individually edited draft. */
export function bulkListingNotes(defaults: BulkUploadDefaults, item: Omit<BulkUploadItem, "files" | "folder"> = {}): string | undefined {
  const cog = defaults.cog.trim();
  const vendooLabels = joinLabels(splitLabels(defaults.labels));
  const sku = item.sku?.trim();
  const notes = {
    ...(cog || vendooLabels ? { cog, vendooLabels } : {}),
    ...(sku ? { sku } : {}),
  };
  const shared = Object.keys(notes).length ? JSON.stringify(notes) : undefined;
  const { garment, measurements = {} } = item;
  if (!garment || !Object.keys(typedMeasurements(garment, measurements)).length) return shared;
  return notesWithMeasurements(shared, garment, measurements);
}

/** Create each draft with its details already attached, then add its photos. */
export async function createBulkPhotoListings(
  items: BulkUploadItem[],
  defaults: BulkUploadDefaults,
  conversations: BulkUploadApi,
): Promise<BulkListingUploadResult[]> {
  const listings: BulkListingUploadResult[] = [];

  for (const item of items) {
    const notes = bulkListingNotes(defaults, item);
    const conv = await conversations.create({
      title: listingTitleForFolder(item.folder),
      ...(notes ? { notes } : {}),
      ...(defaults.boxId ? { box_id: defaults.boxId } : {}),
    });
    const result = await conversations.uploadPhotos(conv.id, item.files);
    const errors = result.errors || [];
    // A file that failed to upload is missing from the result, so match by name.
    const rotations = new Map(
      Array.from(item.rotations || [], ([file, degrees]) => [file.name, degrees] as const),
    );
    for (const photo of result.photos) {
      const degrees = rotations.get(photo.original_filename);
      if (!degrees) continue;
      try {
        await conversations.rotatePhoto(conv.id, photo.id, degrees);
      } catch (err) {
        errors.push(`${photo.original_filename}: ${err instanceof Error ? err.message : "Rotate failed"}`);
      }
    }
    listings.push({
      convId: conv.id,
      folder: item.folder,
      count: result.count,
      errors,
    });
  }

  return listings;
}
