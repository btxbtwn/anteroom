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
  instance.sql.exec("SELECT * FROM oauth_sessions ORDER BY created_at").toArray());
const alarm = () => runInDurableObject(directory, (_, state) => state.storage.getAlarm());

it("deletes uncollected tokens on the alarm without another device request", async () => {
  await directory.startSession("idle", "device", "etsy", "verifier");
  await directory.finishSession("idle", { access_token: "uncollected-test-token" });
  expect((await stored())[0].result).toContain("uncollected-test-token");
  expect(await alarm()).toBe(now + TTL);
  now += TTL;
  expect(await runDurableObjectAlarm(directory)).toBe(true);
  expect(await stored()).toEqual([]);
  expect(await alarm()).toBe(null);
});

it("keeps the earliest deadline when later sign-ins start", async () => {
  const first = now;
  await directory.startSession("first", "device", "ebay");
  now += 30_000;
  await directory.startSession("second", "device", "etsy");
  expect(await alarm()).toBe(first + TTL);
  now = first + TTL;
  await runDurableObjectAlarm(directory);
  expect((await stored()).map((row) => row.state)).toEqual(["second"]);
  expect(await alarm()).toBe(first + 30_000 + TTL);
});

it("cancels the alarm after the last result is collected", async () => {
  await directory.startSession("collected", "device", "etsy");
  await directory.finishSession("collected", { access_token: "test-token" });
  expect((await directory.takeResult("collected", "device")).status).toBe("done");
  expect(await stored()).toEqual([]);
  expect(await alarm()).toBe(null);
});

it("does not save credentials arriving after the sign-in expired", async () => {
  await directory.startSession("expired", "device", "ebay");
  now += TTL;
  await directory.finishSession("expired", { access_token: "late-test-token" });
  expect(await stored()).toEqual([]);
  expect(await alarm()).toBe(null);
});

it("arms expiry for sessions stored before alarms were introduced", async () => {
  await directory.startSession("old", "device", "etsy");
  await runInDurableObject(directory, (_, state) => state.storage.deleteAlarm());
  await evictDurableObject(directory);
  expect(await directory.deviceForToken("unused")).toBe(null);
  expect(await alarm()).toBe(now + TTL);
});
