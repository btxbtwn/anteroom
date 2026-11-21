import type { PhotoUploadResult } from "../../api/types";
import type { ComposerImage } from "./ComposerAttachments";

export interface DraftPhotoUploadDeps {
  uploadPhotos: (files: File[]) => Promise<PhotoUploadResult>;
  invalidatePhotos: () => Promise<void>;
  clearImages: (ids: readonly string[]) => void;
  setError: (message: string) => void;
  sendMessage: (text: string) => unknown;
  generate: () => unknown;
}

/**
 * Promote chat-composer images to the draft's listing photos when the draft has
 * none yet, then kick off generation or send the seller's accompanying note.
 */
export async function uploadComposerDraftPhotos(
  attached: readonly ComposerImage[],
  text: string,
  hasListingJson: boolean,
  deps: DraftPhotoUploadDeps,
): Promise<boolean> {
  try {
    const result = await deps.uploadPhotos(attached.map((image) => image.file));
    if (result.count > 0) {
      await deps.invalidatePhotos();
    }
    const errors = result.errors || [];
    if (result.count === 0) {
      deps.setError(errors.join("; ") || "Could not upload photos.");
      return false;
    }
    if (errors.length) {
      const uploadedCounts = new Map<string, number>();
      for (const photo of result.photos || []) {
        const name = photo.original_filename;
        if (name) uploadedCounts.set(name, (uploadedCounts.get(name) ?? 0) + 1);
      }
      const uploadedIds: string[] = [];
      for (const image of attached) {
        const remaining = uploadedCounts.get(image.file.name) ?? 0;
        if (remaining > 0) {
          uploadedIds.push(image.id);
          uploadedCounts.set(image.file.name, remaining - 1);
        }
      }
      deps.clearImages(uploadedIds);
      deps.setError(errors.join("; "));
      return false;
    }
    deps.clearImages(attached.map((image) => image.id));
    if (text) {
      deps.sendMessage(text);
    } else if (!hasListingJson) {
      deps.generate();
    }
    return true;
  } catch (err) {
    deps.setError(err instanceof Error ? err.message : "Could not upload photos.");
    return false;
  }
}
