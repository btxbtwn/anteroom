import type { SupabaseClient } from "@supabase/supabase-js";

export interface CloudDraft {
  id: string;
  title: string | null;
  notes: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export class CloudRequestError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

export function cloudDrafts(auth: Pick<SupabaseClient, "auth">, request = fetch) {
  async function call<T>(path: string, method = "GET", body?: unknown): Promise<T> {
    const { data: { session }, error } = await auth.auth.getSession();
    if (error || !session) throw new CloudRequestError("Sign in to continue.", 401);
    const response = await request(`/api/drafts${path}`, {
      method,
      headers: { Authorization: `Bearer ${session.access_token}`, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
    if (!response.ok) {
      const message = response.status === 401 ? "Your session expired. Sign in again."
        : response.status === 409 ? "This draft changed on another device. Reload it before saving again."
        : response.status === 404 ? "Draft not found."
        : "Could not save or load your drafts. Please try again.";
      throw new CloudRequestError(message, response.status);
    }
    return response.json() as Promise<T>;
  }
  return {
    list: () => call<CloudDraft[]>(""),
    get: (id: string) => call<CloudDraft>(`/${encodeURIComponent(id)}`),
    create: (title: string) => call<CloudDraft>("", "POST", { title, notes: "" }),
    save: (draft: CloudDraft, title: string, notes: string) => call<CloudDraft>(`/${encodeURIComponent(draft.id)}`, "PATCH", {
      title, notes, expected_updated_at: draft.updated_at,
    }),
  };
}
