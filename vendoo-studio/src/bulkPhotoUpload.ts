import type { ListingPhoto, PhotoUploadResult } from "./api/types";
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
  /** Tag text is seller evidence for generation, saved in the existing seller notes. */
  sizeTag?: string;
  garment?: Garment;
  measurements?: Record<string, string>;
}

interface BulkUploadApi {
  create: (body: { title: string; notes?: string; box_id?: string }) => Promise<{ id: string }>;
  uploadPhotos: (convId: string, files: File[]) => Promise<PhotoUploadResult>;
  rotatePhoto: (convId: string, photoId: string, degrees: PhotoRotation) => Promise<ListingPhoto>;
  photos: (convId: string) => Promise<ListingPhoto[]>;
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
  const sizeTag = item.sizeTag?.trim();
  const notes = {
    ...(cog || vendooLabels ? { cog, vendooLabels } : {}),
    ...(sku ? { sku } : {}),
    ...(sizeTag ? { sellerNotes: `Size tag: ${sizeTag}` } : {}),
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
    let result: PhotoUploadResult;
    try {
      result = await conversations.uploadPhotos(conv.id, item.files);
    } catch (err) {
      // One refused folder (over 20 photos, or every file unreadable) must not
      // stop the folders after it or hide the drafts already started. Hosted
      // uploads go one photo at a time, so a failure can follow saved photos:
      // count what the draft holds so it still generates.
      const message = err instanceof Error ? err.message : "Upload failed";
      const saved = await conversations.photos(conv.id).then((photos) => photos.length, () => null);
      // An unreadable draft may still hold photos: say so rather than call it empty.
      const unknown = saved === null ? " Anteroom couldn't check which of its photos were saved; open the draft to check." : "";
      listings.push({ convId: conv.id, folder: item.folder, count: saved ?? 0, errors: [`${item.folder || "New Listing"}: ${message}${unknown}`] });
      continue;
    }
    const errors = result.errors || [];
    // A file that failed to upload is missing from the result, so match by name.
    const sourceFiles = new Map(item.files.map(file => [file.name, file] as const));
    for (const file of item.files) {
      if (file.webkitRelativePath) sourceFiles.set(file.webkitRelativePath, file);
    }
    for (const photo of result.photos) {
      // Directory uploads can retain their relative path in the multipart
      // filename. Each item is one folder; match its File.name basenames.
      const name = photo.original_filename.split(/[\\/]/).pop() || photo.original_filename;
      const source = sourceFiles.get(photo.original_filename) ?? sourceFiles.get(name);
      const degrees = source && item.rotations?.get(source);
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
