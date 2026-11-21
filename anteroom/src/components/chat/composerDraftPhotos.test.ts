import { describe, expect, it, vi } from "vitest";
import type { ListingPhoto } from "../../api/types";
import type { ComposerImage } from "./ComposerAttachments";
import { uploadComposerDraftPhotos } from "./composerDraftPhotos";

function makeImage(id: string, name: string): ComposerImage {
  return { id, file: new File(["img"], name, { type: "image/jpeg" }) };
}

function makePhoto(id: string, name: string): ListingPhoto {
  return {
    id,
    conversation_id: "conv-1",
    original_filename: name,
    mime_type: "image/jpeg",
    size_bytes: 3,
    display_order: 0,
    width: 100,
    height: 100,
    created_at: "2026-11-21T00:00:00Z",
    url: `/api/photos/${id}`,
  };
}

describe("uploadComposerDraftPhotos", () => {
  it("uploads attached images as listing photos and generates when there is no text or listing yet", async () => {
    const attached = [makeImage("img-1", "front.jpg"), makeImage("img-2", "tag.jpg")];
    const uploadPhotos = vi.fn(async () => ({
      ok: true,
      count: 2,
      photos: [makePhoto("p1", "front.jpg"), makePhoto("p2", "tag.jpg")],
    }));
    const invalidatePhotos = vi.fn(async () => {});
    const clearImages = vi.fn();
    const setError = vi.fn();
    const sendMessage = vi.fn();
    const generate = vi.fn();

    const ok = await uploadComposerDraftPhotos(attached, "", false, {
      uploadPhotos,
      invalidatePhotos,
      clearImages,
      setError,
      sendMessage,
      generate,
    });

    expect(ok).toBe(true);
    expect(uploadPhotos).toHaveBeenCalledWith(attached.map((i) => i.file));
    expect(invalidatePhotos).toHaveBeenCalledTimes(1);
    expect(clearImages).toHaveBeenCalledWith(["img-1", "img-2"]);
    expect(generate).toHaveBeenCalledTimes(1);
    expect(sendMessage).not.toHaveBeenCalled();
    expect(setError).not.toHaveBeenCalled();
  });

  it("uploads attached images and sends the seller's note without duplicating chat attachments", async () => {
    const attached = [makeImage("img-1", "front.jpg")];
    const sendMessage = vi.fn();
    const generate = vi.fn();

    const ok = await uploadComposerDraftPhotos(attached, "Size M, tiny pinhole on cuff", false, {
      uploadPhotos: vi.fn(async () => ({ ok: true, count: 1, photos: [makePhoto("p1", "front.jpg")] })),
      invalidatePhotos: vi.fn(async () => {}),
      clearImages: vi.fn(),
      setError: vi.fn(),
      sendMessage,
      generate,
    });

    expect(ok).toBe(true);
    expect(sendMessage).toHaveBeenCalledWith("Size M, tiny pinhole on cuff");
    expect(generate).not.toHaveBeenCalled();
  });

  it("uploads images without overwriting an existing listing when text is empty", async () => {
    const attached = [makeImage("img-1", "front.jpg")];
    const sendMessage = vi.fn();
    const generate = vi.fn();

    const ok = await uploadComposerDraftPhotos(attached, "", true, {
      uploadPhotos: vi.fn(async () => ({ ok: true, count: 1, photos: [makePhoto("p1", "front.jpg")] })),
      invalidatePhotos: vi.fn(async () => {}),
      clearImages: vi.fn(),
      setError: vi.fn(),
      sendMessage,
      generate,
    });

    expect(ok).toBe(true);
    expect(generate).not.toHaveBeenCalled();
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("clears only uploaded images and surfaces the error on partial upload failure", async () => {
    const attached = [makeImage("img-1", "good.jpg"), makeImage("img-2", "huge.jpg")];
    const clearImages = vi.fn();
    const setError = vi.fn();
    const generate = vi.fn();
    const invalidatePhotos = vi.fn(async () => {});

    const ok = await uploadComposerDraftPhotos(attached, "", false, {
      uploadPhotos: vi.fn(async () => ({
        ok: false,
        count: 1,
        photos: [makePhoto("p1", "good.jpg")],
        errors: ["huge.jpg: choose a photo smaller than 10 MB."],
      })),
      invalidatePhotos,
      clearImages,
      setError,
      sendMessage: vi.fn(),
      generate,
    });

    expect(ok).toBe(false);
    expect(invalidatePhotos).toHaveBeenCalledTimes(1);
    expect(clearImages).toHaveBeenCalledWith(["img-1"]);
    expect(setError).toHaveBeenCalledWith("huge.jpg: choose a photo smaller than 10 MB.");
    expect(generate).not.toHaveBeenCalled();
  });

  it("keeps images and surfaces the error when all uploads fail or throw", async () => {
    const attached = [makeImage("img-1", "bad.jpg")];
    const clearImages = vi.fn();
    const setError = vi.fn();
    const generate = vi.fn();

    const ok = await uploadComposerDraftPhotos(attached, "", false, {
      uploadPhotos: vi.fn(async () => {
        throw new Error("Network offline");
      }),
      invalidatePhotos: vi.fn(async () => {}),
      clearImages,
      setError,
      sendMessage: vi.fn(),
      generate,
    });

    expect(ok).toBe(false);
    expect(clearImages).not.toHaveBeenCalled();
    expect(setError).toHaveBeenCalledWith("Network offline");
    expect(generate).not.toHaveBeenCalled();
  });
});
