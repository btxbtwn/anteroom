import type { SupabaseClient } from "@supabase/supabase-js";
import { CloudRequestError } from "./cloudDrafts";
export interface CloudPhoto { id: string; width: number | null; height: number | null; size_bytes: number }

export function cloudPhotos(client: Pick<SupabaseClient, "auth">, draftId: string, request = fetch) {
  const root = `/api/drafts/${encodeURIComponent(draftId)}/photos`;
  async function call(path: string, method = "GET", body?: File, signal?: AbortSignal) {
    const { data: { session }, error } = await client.auth.getSession();
    if (error || !session) throw new CloudRequestError("Sign in to continue.", 401);
    const response = await request(root + path, { method, body, signal, cache: "no-store", headers: {
      Authorization: `Bearer ${session.access_token}`, ...(body ? { "Content-Type": "application/octet-stream" } : {}),
    } });
    if (!response.ok) throw new CloudRequestError(response.status === 413 ? "Choose a photo smaller than 10 MB."
      : response.status === 422 ? "Choose a JPEG, PNG or WebP photo under 25 megapixels. A draft can have up to 30 photos."
      : response.status === 401 ? "Your session expired. Sign in again."
      : "Could not upload or load your photos. Please try again.", response.status);
    return response;
  }
  return {
    list: async (): Promise<CloudPhoto[]> => (await call("")).json(),
    upload: async (file: File): Promise<CloudPhoto> => {
      if (file.size > 10 * 1024 * 1024) throw new CloudRequestError("Choose a photo smaller than 10 MB.", 413);
      return (await call("", "POST", file)).json();
    },
    read: async (id: string, signal?: AbortSignal): Promise<Blob> => (await call(`/${encodeURIComponent(id)}`, "GET", undefined, signal)).blob(),
    remove: async (id: string) => { await call(`/${encodeURIComponent(id)}`, "DELETE"); },
  };
}
