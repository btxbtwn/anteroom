import { afterEach, expect, it, vi } from "vitest";
import { api } from "./client";
import { installHostedApiSession } from "./transport";

let dispose: (() => void) | undefined;
afterEach(() => { dispose?.(); dispose = undefined; vi.unstubAllGlobals(); });

it("loads the canonical marketplace form through authenticated HTTP without changing its rows", async () => {
  const form = { marketplace: "depop", fields: [
    { path: "depop_specifics.description", label: "Description", section: "Info", value: "Private copy" },
    { path: "direct_category_ids.depop", label: "Category", section: "Info", width: "half", options: ["Jeans"] },
  ] };
  const fetch = vi.fn(async () => new Response(JSON.stringify(form), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  dispose = installHostedApiSession({ origin: "https://anteroom.example", accessToken: async () => "seller-token", unauthorized: vi.fn() });
  expect(await api.nativeMarketplaces.form("owned", "depop", "revision")).toEqual(form);
  const [url, options] = fetch.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("https://anteroom.example/api/conversations/owned/native-marketplaces/depop/form?revision_id=revision");
  expect(new Headers(options.headers).get("Authorization")).toBe("Bearer seller-token");
});
