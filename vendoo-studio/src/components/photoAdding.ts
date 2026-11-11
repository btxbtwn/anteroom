import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useWorkspaceRuntime } from "../app/runtime";

/** What the website says where it offers no upload, the same as the iPhone app. */
export const DRAFT_ONLY_PHOTOS = "New photos can be added while this listing is a draft.";

/**
 * Hosted photo uploads accept drafts only (`routes/hosted_photos.py`
 * `require_draft`); the Mac adds photos to any listing. A status not read yet
 * keeps the buttons, so they do not flicker while the listing loads.
 */
export function canAddPhotos(hosted: boolean, status: string | null | undefined): boolean {
  return !hosted || !status || status === "draft";
}

/** Whether this listing takes new photos here. Shares the editor's conversation read. */
export function useCanAddPhotos(convId: string): boolean {
  const runtime = useWorkspaceRuntime();
  const { data: conversation } = useQuery({
    queryKey: ["conversation", convId],
    queryFn: ({ signal }) => api.conversations.get(convId, { signal }),
    enabled: runtime.hosted,
  });
  return canAddPhotos(runtime.hosted, conversation?.status);
}
