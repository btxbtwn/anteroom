import { env } from "cloudflare:workers";
import { evictDurableObject, runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const TTL = 15 * 60 * 1000;
let directory;
let now;

beforeEach(() => {
  now = Date.now() + 60_000;
  vi.spyOn(Date, "now").mockImplementation(() => now);
  directory = env.DIRECTORY.get(env.DIRECTORY.idFromName(crypto.randomUUID()));
});
afterEach(() => vi.restoreAllMocks());

const stored = () => runInDurableObject(directory, (instance) =>
  instance.sql.exec("SELECT * FROM sign_ins ORDER BY created_at").toArray());
const BACK = "http://127.0.0.1:4318/api/marketplace-accounts/etsy/finish";
const alarm = () => runInDurableObject(directory, (_, state) => state.storage.getAlarm());

it("deletes uncollected tokens on the alarm without another device request", async () => {
  await directory.startSession("idle", "device", "etsy", BACK, "verifier");
  await directory.finishSession("idle", { link: [], result: { access_token: "uncollected-test-token" } }, "claim-hash");
  expect((await stored())[0].result).toContain("uncollected-test-token");
  expect(await alarm()).toBe(now + TTL);
  now += TTL;
  expect(await runDurableObjectAlarm(directory)).toBe(true);
  expect(await stored()).toEqual([]);
  expect(await alarm()).toBe(null);
});

it("keeps the earliest deadline when later sign-ins start", async () => {
  const first = now;
  await directory.startSession("first", "device", "ebay", BACK);
  now += 30_000;
  await directory.startSession("second", "device", "etsy", BACK);
  expect(await alarm()).toBe(first + TTL);
  now = first + TTL;
  await runDurableObjectAlarm(directory);
  expect((await stored()).map((row) => row.state)).toEqual(["second"]);
  expect(await alarm()).toBe(first + 30_000 + TTL);
});

it("cancels the alarm after the last result is collected", async () => {
  await directory.startSession("collected", "device", "etsy", BACK);
  await directory.finishSession("collected", { link: [], result: { access_token: "test-token" } }, "claim-hash");
  expect((await directory.takeResult("collected", "device")).status).toBe("pending");
  expect((await directory.takeResult("collected", "device", "wrong-hash")).status).toBe("refused");
  expect((await directory.takeResult("collected", "device", "claim-hash")).status).toBe("done");
  expect(await stored()).toEqual([]);
  expect(await alarm()).toBe(null);
});

it("does not save credentials arriving after the sign-in expired", async () => {
  await directory.startSession("expired", "device", "ebay", BACK);
  now += TTL;
  await directory.finishSession("expired", { link: [], result: { access_token: "late-test-token" } }, "claim-hash");
  expect(await stored()).toEqual([]);
  expect(await alarm()).toBe(null);
});

it("arms expiry for sessions stored before alarms were introduced", async () => {
  await directory.startSession("old", "device", "etsy", BACK);
  await runInDurableObject(directory, (_, state) => state.storage.deleteAlarm());
  await evictDurableObject(directory);
  expect(await directory.deviceForToken("unused")).toBe(null);
  expect(await alarm()).toBe(now + TTL);
});
