import { useCallback, useSyncExternalStore, type SetStateAction } from "react";
import type { ComposerImage } from "./ComposerAttachments";

export interface ComposerDraft {
  text: string;
  images: ComposerImage[];
  listingPhotoBatch?: "uploading" | "retry";
}

const EMPTY_DRAFT: ComposerDraft = { text: "", images: [] };
const drafts = new Map<string, ComposerDraft>();
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getComposerDraft(key: string): ComposerDraft {
  return drafts.get(key) ?? EMPTY_DRAFT;
}

export function hasComposerDraft(key: string): boolean {
  const current = drafts.get(key);
  return Boolean(current && (current.text.trim().length > 0 || current.images.length > 0));
}

export function setComposerDraftText(key: string, text: string): void {
  const current = drafts.get(key) ?? EMPTY_DRAFT;
  if (current.text === text) return;
  if (!text && !current.images.length && !current.listingPhotoBatch) {
    drafts.delete(key);
  } else {
    drafts.set(key, { ...current, text });
  }
  emit();
}

export function setComposerDraftImages(key: string, images: ComposerImage[]): void {
  const current = drafts.get(key) ?? EMPTY_DRAFT;
  if (current.images === images) return;
  if (!current.text && !images.length && !current.listingPhotoBatch) {
    drafts.delete(key);
  } else {
    drafts.set(key, { ...current, images });
  }
  emit();
}

/** Keep the batch intent across remounts and while a failed image is replaced. */
export function setComposerListingPhotoBatch(key: string, batch: ComposerDraft["listingPhotoBatch"]): void {
  const current = getComposerDraft(key);
  if (current.listingPhotoBatch === batch) return;
  if (!batch && !current.text && !current.images.length) {
    drafts.delete(key);
  } else {
    drafts.set(key, { ...current, listingPhotoBatch: batch });
  }
  emit();
}

export function removeComposerDraftImages(key: string, ids: readonly string[]): void {
  if (!ids.length) return;
  const current = drafts.get(key);
  if (!current || !current.images.length) return;
  const sent = new Set(ids);
  const remaining = current.images.filter((image) => !sent.has(image.id));
  if (remaining.length === current.images.length) return;
  setComposerDraftImages(key, remaining);
}

/** Restores attachments from a failed send without duplicating or dropping newer images. */
export function restoreComposerDraftImages(key: string, restored: readonly ComposerImage[]): void {
  if (!restored.length) return;
  const current = drafts.get(key) ?? EMPTY_DRAFT;
  const existingIds = new Set(current.images.map((image) => image.id));
  const missing = restored.filter((image) => !existingIds.has(image.id));
  if (!missing.length) return;
  setComposerDraftImages(key, [...missing, ...current.images]);
}

export function clearComposerDraft(key: string): void {
  if (!drafts.delete(key)) return;
  emit();
}

/**
 * Moves unsent remainder from `fromKey` to `toKey` (e.g. when a new Assistant
 * chat creates its thread ID). Submitted text is cleared rather than copied,
 * while newer text and any remaining images transfer to `toKey`.
 */
export function transferComposerDraftRemainder(fromKey: string, toKey: string, submittedText: string | null): void {
  if (fromKey === toKey) return;
  const from = drafts.get(fromKey);
  if (!from) return;
  const keepText = submittedText !== null && from.text.trim() === submittedText.trim() ? "" : from.text;
  const keepImages = from.images;
  drafts.delete(fromKey);
  if (keepText || keepImages.length) {
    const existing = drafts.get(toKey) ?? EMPTY_DRAFT;
    drafts.set(toKey, {
      text: existing.text || keepText,
      images: existing.images.length ? [...existing.images, ...keepImages] : keepImages,
    });
  }
  emit();
}

/** Test helper to isolate draft state across test cases. */
export function resetComposerDrafts(): void {
  if (!drafts.size) return;
  drafts.clear();
  emit();
}

/** Unsent composer text for one conversation or thread, preserved across switches until sent. */
export function useComposerDraftText(key: string): [string, (next: SetStateAction<string>) => void] {
  const text = useSyncExternalStore(
    subscribe,
    () => getComposerDraft(key).text,
    () => getComposerDraft(key).text,
  );
  const setText = useCallback((next: SetStateAction<string>) => {
    const prev = getComposerDraft(key).text;
    const resolved = typeof next === "function" ? next(prev) : next;
    setComposerDraftText(key, resolved);
  }, [key]);
  return [text, setText];
}

/** Unsent composer images for one conversation or thread, preserved across switches until sent. */
export function useComposerDraftImages(key: string): [ComposerImage[], (next: ComposerImage[]) => void] {
  const images = useSyncExternalStore(
    subscribe,
    () => getComposerDraft(key).images,
    () => getComposerDraft(key).images,
  );
  const setImages = useCallback((next: ComposerImage[]) => {
    setComposerDraftImages(key, next);
  }, [key]);
  return [images, setImages];
}
