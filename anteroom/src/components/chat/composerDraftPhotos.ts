import type { ComposerImage } from "./ComposerAttachments";
import type { ComposerDraft } from "./composerDrafts";

export type ComposerImageRoute = "busy" | "wait" | "listing_photos" | "chat_attachments";

/**
 * Decide how chat-composer images should be routed when the seller presses Send.
 * Keeps images in the composer while the draft's photos query is still loading,
 * promotes them to listing photos on empty drafts (or active retry batches), and
 * otherwise sends them as reference chat attachments.
 */
export function decideComposerImageRoute(
  photosCount: number | undefined,
  listingPhotoBatch: ComposerDraft["listingPhotoBatch"],
): ComposerImageRoute {
  if (listingPhotoBatch === "uploading") return "busy";
  if (listingPhotoBatch === "retry") return "listing_photos";
  if (photosCount === undefined) return "wait";
  return photosCount === 0 ? "listing_photos" : "chat_attachments";
}

export interface DraftPhotoUploadDeps {
  uploadPhoto: (file: File) => Promise<void>;
  invalidatePhotos: () => Promise<void>;
  clearImages: (ids: readonly string[]) => void;
  setError: (message: string) => void;
  sendMessage: (text: string) => unknown;
  generate: () => unknown;
  hasListingCopy: () => boolean | Promise<boolean>;
}

/**
 * Promote chat-composer images to the draft's listing photos when the draft has
 * none yet, then kick off generation (only if the draft still has no listing
 * copy after upload completes) or send the seller's accompanying note.
 */
export async function uploadComposerDraftPhotos(
  attached: readonly ComposerImage[],
  text: string,
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
  } else if (!(await deps.hasListingCopy())) {
    deps.generate();
  }
  return true;
}
