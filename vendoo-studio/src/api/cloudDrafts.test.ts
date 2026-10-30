import { expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import { cloudDrafts } from "./cloudDrafts";

function session(token: string | null) {
  return { auth: { getSession: vi.fn().mockResolvedValue({ data: { session: token ? { access_token: token } : null }, error: null }) } } as unknown as Pick<SupabaseClient, "auth">;
}

it("never sends a draft request without a session", async () => {
  const request = vi.fn();
  await expect(cloudDrafts(session(null), request).create("Coat")).rejects.toMatchObject({ status: 401 });
  expect(request).not.toHaveBeenCalled();
});

it("reads the current session for each request instead of retaining the previous seller's token", async () => {
  const auth = session("alice");
  const request = vi.fn().mockResolvedValue(new Response("[]", {status:200}));
  const api = cloudDrafts(auth, request);
  await api.list();
  vi.mocked(auth.auth.getSession).mockResolvedValueOnce({data:{session:{access_token:"bob"}},error:null} as never);
  request.mockResolvedValueOnce(new Response("[]", {status:200}));
  await api.list();
  expect(request.mock.calls[0][1].headers.Authorization).toBe("Bearer alice");
  expect(request.mock.calls[1][1].headers.Authorization).toBe("Bearer bob");
  expect(request.mock.calls[1][1].cache).toBe("no-store");
});

it("preserves the server version on saves and reports conflicts without retrying a mutation", async () => {
  const request = vi.fn().mockResolvedValue(new Response("{}", {status:409}));
  const api = cloudDrafts(session("alice"), request);
  const draft = { id:"draft",title:"Coat",notes:"",created_at:"2026-10-30T00:00:00",updated_at:"2026-10-30T00:00:00" };
  await expect(api.save(draft,"New coat","Keep notes")).rejects.toMatchObject({status:409});
  expect(request).toHaveBeenCalledOnce();
  expect(JSON.parse(request.mock.calls[0][1].body)).toEqual({title:"New coat",notes:"Keep notes",expected_updated_at:draft.updated_at});
});

it("does not expose upstream diagnostics or credentials in error messages", async () => {
  const request = vi.fn().mockResolvedValue(new Response('{"detail":"secret diagnostic"}', {status:500}));
  await expect(cloudDrafts(session("private-token"),request).list()).rejects.toThrow("Could not save or load your drafts.");
});
