import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { DEVICE_MAX_AGE_MS, KEPT_ON_DEVICE, keepReadsForDevice, keepsOnDevice } from "./deviceCache";

async function cached(client: QueryClient, queryKey: unknown[], fail = false) {
  await client.prefetchQuery({ queryKey, queryFn: () => (fail ? Promise.reject(new Error("offline")) : Promise.resolve({ ok: true })), retry: false });
  return client.getQueryCache().find({ queryKey, exact: true })!;
}

describe("what the hosted app keeps on the device", () => {
  it("keeps the inventory and account-wide page reads", async () => {
    const client = new QueryClient();
    for (const key of [["conversations"], ["sourcing"], ["automations"], ["sales-ledger", { start: "2026-10-10" }, 1], ["settings-ui"]]) {
      expect(keepsOnDevice(await cached(client, key)), JSON.stringify(key)).toBe(true);
    }
  });

  it("never keeps chats, photos, jobs, listing details or failed reads", async () => {
    const client = new QueryClient();
    for (const key of [["messages", "a"], ["assistant", "threads"], ["photos", "a"], ["jobs", "a"], ["queue"], ["status"], ["listing", "a"], ["listing-fields", "a"], ["conversation", "a"]]) {
      expect(keepsOnDevice(await cached(client, key)), JSON.stringify(key)).toBe(false);
    }
    expect(keepsOnDevice(await cached(client, ["conversations"], true))).toBe(false);
  });

  it("holds kept reads in memory as long as their saved copy is used", () => {
    const client = new QueryClient();
    keepReadsForDevice(client);
    for (const first of KEPT_ON_DEVICE) expect(client.getQueryDefaults([first, "x"]).gcTime).toBe(DEVICE_MAX_AGE_MS);
    expect(client.getQueryDefaults(["messages", "a"]).gcTime).toBeUndefined();
  });
});
