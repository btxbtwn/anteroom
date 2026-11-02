import { useSyncExternalStore } from "react";

const pending = new Map<string, number>();
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};

export function photosUploading(convId: string): boolean {
  return (pending.get(convId) ?? 0) > 0;
}

export function usePhotosUploading(convId: string): boolean {
  return useSyncExternalStore(subscribe, () => photosUploading(convId), () => false);
}

export async function trackPhotoUpload<T>(convId: string, upload: () => Promise<T>): Promise<T> {
  pending.set(convId, (pending.get(convId) ?? 0) + 1);
  listeners.forEach(listener => listener());
  try {
    return await upload();
  } finally {
    const remaining = (pending.get(convId) ?? 1) - 1;
    if (remaining) pending.set(convId, remaining);
    else pending.delete(convId);
    listeners.forEach(listener => listener());
  }
}
