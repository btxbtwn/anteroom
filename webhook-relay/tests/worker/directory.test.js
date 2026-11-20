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

it("atomically removes etsy-user only when the last shop for (device, user) is unlinked", async () => {
  await directory.link("etsy", "101", "dev-a", "Shop101", "user-1");
  await directory.link("etsy", "102", "dev-a", "Shop102", "user-1");
  await directory.link("etsy-user", "user-1", "dev-a", "Shop102");
  await directory.link("etsy", "201", "dev-a", "Shop201", "user-2");
  await directory.link("etsy-user", "user-2", "dev-a", "Shop201");

  // Another device cannot unlink dev-a's shop or etsy-user.
  await directory.unlink("etsy", "101", "dev-b");
  expect(await directory.deviceForAccount("etsy", "101")).toBe("dev-a");
  expect(await directory.deviceForAccount("etsy-user", "user-1")).toBe("dev-a");

  // Unlinking the first of two shops for user-1 keeps user-1 linked.
  await directory.unlink("etsy", "101", "dev-a");
  expect(await directory.deviceForAccount("etsy", "101")).toBe(null);
  expect(await directory.deviceForAccount("etsy-user", "user-1")).toBe("dev-a");
  expect(await directory.deviceForAccount("etsy-user", "user-2")).toBe("dev-a");

  // Unlinking the last shop for user-1 removes user-1 but keeps user-2.
  await directory.unlink("etsy", "102", "dev-a");
  expect(await directory.deviceForAccount("etsy", "102")).toBe(null);
  expect(await directory.deviceForAccount("etsy-user", "user-1")).toBe(null);
  expect(await directory.deviceForAccount("etsy-user", "user-2")).toBe("dev-a");
});

it("migrates pre-existing accounts tables and cleans up legacy etsy-user links on unlink", async () => {
  // Simulate a Durable Object created before the etsy_user_id column existed.
  await runInDurableObject(directory, (instance) => {
    instance.sql.exec("DROP TABLE accounts");
    instance.sql.exec(`
      CREATE TABLE accounts (
        marketplace TEXT NOT NULL,
        account_id TEXT NOT NULL,
        device_id TEXT NOT NULL,
        label TEXT,
        linked_at INTEGER NOT NULL,
        PRIMARY KEY (marketplace, account_id)
      );
      CREATE INDEX accounts_by_device ON accounts (device_id);
      INSERT INTO accounts (marketplace, account_id, device_id, label, linked_at)
      VALUES
        ('etsy', 'legacy-shop', 'dev-a', 'LegacyShop', 1000),
        ('etsy-user', 'legacy-user', 'dev-a', 'LegacyShop', 1000);
    `);
  });
  await evictDurableObject(directory);

  // Active legacy shop and user continue working after migration.
  expect(await directory.deviceForAccount("etsy", "legacy-shop")).toBe("dev-a");
  expect(await directory.deviceForAccount("etsy-user", "legacy-user")).toBe("dev-a");

  // A new tracked shop on the same device is preserved when the legacy shop is unlinked.
  await directory.link("etsy", "new-shop", "dev-a", "NewShop", "new-user");
  await directory.link("etsy-user", "new-user", "dev-a", "NewShop");

  await directory.unlink("etsy", "legacy-shop", "dev-a");
  expect(await directory.deviceForAccount("etsy", "legacy-shop")).toBe(null);
  expect(await directory.deviceForAccount("etsy-user", "legacy-user")).toBe(null);
  expect(await directory.deviceForAccount("etsy", "new-shop")).toBe("dev-a");
  expect(await directory.deviceForAccount("etsy-user", "new-user")).toBe("dev-a");
});

