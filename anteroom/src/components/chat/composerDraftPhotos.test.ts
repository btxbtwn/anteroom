import { describe, expect, it, vi } from "vitest";
import type { ComposerImage } from "./ComposerAttachments";
import { decideComposerImageRoute, uploadComposerDraftPhotos } from "./composerDraftPhotos";

function makeImage(id: string, name: string): ComposerImage {
  return { id, file: new File(["img"], name, { type: "image/jpeg" }) };
}

describe("decideComposerImageRoute", () => {
  it("defers sending images while the photos query is still unresolved", () => {
    expect(decideComposerImageRoute(undefined, undefined)).toBe("wait");
  });

  it("routes images as listing photos on empty drafts and on partial-upload retries", () => {
    expect(decideComposerImageRoute(0, undefined)).toBe("listing_photos");
    expect(decideComposerImageRoute(2, "retry")).toBe("listing_photos");
    expect(decideComposerImageRoute(undefined, "retry")).toBe("listing_photos");
  });

  it("routes images as chat attachments when the draft already has photos and no retry batch", () => {
    expect(decideComposerImageRoute(2, undefined)).toBe("chat_attachments");
  });

  it("blocks duplicate sends while a listing-photo batch is already uploading", () => {
    expect(decideComposerImageRoute(0, "uploading")).toBe("busy");
    expect(decideComposerImageRoute(undefined, "uploading")).toBe("busy");
  });
});

describe("uploadComposerDraftPhotos", () => {
  it("uploads attached images as listing photos and generates when there is no text or listing yet", async () => {
    const attached = [makeImage("img-1", "front.jpg"), makeImage("img-2", "tag.jpg")];
    const uploadPhoto = vi.fn(async (_file: File) => {});
    const invalidatePhotos = vi.fn(async () => {});
    const clearImages = vi.fn();
    const setError = vi.fn();
    const sendMessage = vi.fn();
    const generate = vi.fn();
    const hasListingCopy = vi.fn(() => false);

    const ok = await uploadComposerDraftPhotos(attached, "", {
      uploadPhoto,
      invalidatePhotos,
      clearImages,
      setError,
      sendMessage,
      generate,
      hasListingCopy,
    });

    expect(ok).toBe(true);
    expect(uploadPhoto.mock.calls).toEqual(attached.map(({ file }) => [file]));
    expect(invalidatePhotos).toHaveBeenCalledTimes(1);
    expect(clearImages).toHaveBeenCalledWith(["img-1", "img-2"]);
    expect(hasListingCopy).toHaveBeenCalledTimes(1);
    expect(generate).toHaveBeenCalledTimes(1);
    expect(sendMessage).not.toHaveBeenCalled();
    expect(setError).not.toHaveBeenCalled();
  });

  it("uploads attached images and sends the seller's note without duplicating chat attachments", async () => {
    const attached = [makeImage("img-1", "front.jpg")];
    const sendMessage = vi.fn();
    const generate = vi.fn();
    const hasListingCopy = vi.fn(() => false);

    const ok = await uploadComposerDraftPhotos(attached, "Size M, tiny pinhole on cuff", {
      uploadPhoto: vi.fn(async () => {}),
      invalidatePhotos: vi.fn(async () => {}),
      clearImages: vi.fn(),
      setError: vi.fn(),
      sendMessage,
      generate,
      hasListingCopy,
    });

    expect(ok).toBe(true);
    expect(sendMessage).toHaveBeenCalledWith("Size M, tiny pinhole on cuff");
    expect(generate).not.toHaveBeenCalled();
    expect(hasListingCopy).not.toHaveBeenCalled();
  });

  it("re-reads listing copy after upload so saved copy loaded during upload is not overwritten", async () => {
    const attached = [makeImage("img-1", "front.jpg")];
    const sendMessage = vi.fn();
    const generate = vi.fn();
    let listingLoaded = false;

    const ok = await uploadComposerDraftPhotos(attached, "", {
      uploadPhoto: vi.fn(async () => {
        // Simulates the listing query resolving with saved copy mid-upload.
        listingLoaded = true;
      }),
      invalidatePhotos: vi.fn(async () => {}),
      clearImages: vi.fn(),
      setError: vi.fn(),
      sendMessage,
      generate,
      hasListingCopy: () => listingLoaded,
    });

    expect(ok).toBe(true);
    expect(generate).not.toHaveBeenCalled();
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("clears only uploaded images and surfaces the error on partial upload failure", async () => {
    const attached = [makeImage("img-1", "image.jpg"), makeImage("img-2", "image.jpg")];
    const clearImages = vi.fn();
    const setError = vi.fn();
    const generate = vi.fn();
    const invalidatePhotos = vi.fn(async () => {});

    const ok = await uploadComposerDraftPhotos(attached, "", {
      uploadPhoto: vi.fn()
        .mockRejectedValueOnce(new Error("image.jpg: choose a photo smaller than 10 MB."))
        .mockResolvedValueOnce(undefined),
      invalidatePhotos,
      clearImages,
      setError,
      sendMessage: vi.fn(),
      generate,
      hasListingCopy: () => false,
    });

    expect(ok).toBe(false);
    expect(invalidatePhotos).toHaveBeenCalledTimes(1);
    expect(clearImages).toHaveBeenCalledWith(["img-2"]);
    expect(setError).toHaveBeenCalledWith("image.jpg: choose a photo smaller than 10 MB.");
    expect(generate).not.toHaveBeenCalled();
  });

  it("keeps images and surfaces the error when all uploads fail or throw", async () => {
    const attached = [makeImage("img-1", "bad.jpg")];
    const clearImages = vi.fn();
    const setError = vi.fn();
    const generate = vi.fn();

    const ok = await uploadComposerDraftPhotos(attached, "", {
      uploadPhoto: vi.fn(async () => {
        throw new Error("Network offline");
      }),
      invalidatePhotos: vi.fn(async () => {}),
      clearImages,
      setError,
      sendMessage: vi.fn(),
      generate,
      hasListingCopy: () => false,
    });

    expect(ok).toBe(false);
    expect(clearImages).not.toHaveBeenCalled();
    expect(setError).toHaveBeenCalledWith("Network offline");
    expect(generate).not.toHaveBeenCalled();
  });
});
