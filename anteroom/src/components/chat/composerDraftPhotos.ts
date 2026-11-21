import type { ComposerImage } from "./ComposerAttachments";

export interface DraftPhotoUploadDeps {
  uploadPhoto: (file: File) => Promise<void>;
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
  const uploadedIds: string[] = [];
  const errors: string[] = [];
  for (const image of attached) {
    try {
      await deps.uploadPhoto(image.file);
      uploadedIds.push(image.id);
    } catch (err) {
      errors.push(err instanceof Error ? err.message : `${image.file.name}: could not upload this photo.`);
    }
  }
  if (uploadedIds.length) {
    // A failed refresh must not put acknowledged writes back on the retry path.
    deps.clearImages(uploadedIds);
    try {
      await deps.invalidatePhotos();
    } catch {
      errors.push("Photos were saved, but the photo list could not be refreshed.");
    }
  }
  if (errors.length || !uploadedIds.length) {
    deps.setError(errors.join("; ") || "Could not upload photos.");
    return false;
  }
  if (text) {
    deps.sendMessage(text);
  } else if (!hasListingJson) {
    deps.generate();
  }
  return true;
}
