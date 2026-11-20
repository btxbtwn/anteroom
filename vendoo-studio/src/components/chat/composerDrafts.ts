import { useCallback, useSyncExternalStore, type SetStateAction } from "react";
import type { ComposerImage } from "./ComposerAttachments";

export interface ComposerDraft {
  text: string;
  images: ComposerImage[];
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
  if (!text && !current.images.length) {
    drafts.delete(key);
  } else {
    drafts.set(key, { text, images: current.images });
  }
  emit();
}

export function setComposerDraftImages(key: string, images: ComposerImage[]): void {
  const current = drafts.get(key) ?? EMPTY_DRAFT;
  if (current.images === images) return;
  if (!current.text && !images.length) {
    drafts.delete(key);
  } else {
    drafts.set(key, { text: current.text, images });
  }
  emit();
}

export function clearComposerDraft(key: string): void {
  if (!drafts.delete(key)) return;
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
