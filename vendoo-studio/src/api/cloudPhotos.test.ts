import { describe, expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import { cloudPhotos } from "./cloudPhotos";

describe("private cloud photos", () => {
  function auth(token: string | null) {
    return { auth: { getSession: vi.fn(async () => ({ data: { session: token ? { access_token: token } : null }, error: null })) } } as unknown as Pick<SupabaseClient, "auth">;
  }
  it("sends raw bytes with the current session and no token in a URL", async () => {
    const request = vi.fn(async () => new Response(JSON.stringify({ id: "photo" }), { status: 201 }));
    const client = auth("seller-session");
    const file = new File(["bytes"], "coat.jpg", { type: "image/jpeg" });
    await cloudPhotos(client, "draft", request).upload(file);
    expect(request).toHaveBeenCalledWith("/api/drafts/draft/photos", expect.objectContaining({
      method: "POST", body: file, cache: "no-store", headers: { Authorization: "Bearer seller-session", "Content-Type": "application/octet-stream" },
    }));
  });
  it("refuses unsigned requests and oversized files before any upload", async () => {
    const request = vi.fn();
    await expect(cloudPhotos(auth(null), "draft", request).read("photo")).rejects.toMatchObject({ status: 401 });
    await expect(cloudPhotos(auth("session"), "draft", request).upload(new File([new Uint8Array(10 * 1024 * 1024 + 1)], "large.jpg"))).rejects.toMatchObject({ status: 413 });
    expect(request).not.toHaveBeenCalled();
  });
  it("does not retry failed mutations or reveal private diagnostics", async () => {
    const request = vi.fn(async () => new Response("PRIVATE server diagnostic", { status: 503 }));
    await expect(cloudPhotos(auth("session"), "draft", request).remove("photo")).rejects.toThrow("Could not upload or load your photos");
    expect(request).toHaveBeenCalledTimes(1);
  });
});
