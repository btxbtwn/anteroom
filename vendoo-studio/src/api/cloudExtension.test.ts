import { describe, expect, it } from "vitest";
import { cloudExtension } from "./cloudExtension";
const ORIGIN = "https://peaceful-meadowstone-6944.fly.dev";
const ACCOUNT = { id: "11111111-1111-4111-8111-111111111111", email: "alice@example.com", access_token: "never-forward-this" };
function channel(origin = ORIGIN) {
  const listeners = new Set<(event: MessageEvent) => void>();
  const timers = new Map<number, () => void>();
  const sent: { data: Record<string, unknown>; origin: string }[] = [];
  const target = {
    location: { origin },
    addEventListener: (_name: string, callback: (event: MessageEvent) => void) => listeners.add(callback),
    removeEventListener: (_name: string, callback: (event: MessageEvent) => void) => listeners.delete(callback),
    setTimeout: (callback: () => void) => { const id = timers.size + 1; timers.set(id, callback); return id; },
    clearTimeout: (id: number) => timers.delete(id),
    postMessage: (data: Record<string, unknown>, origin: string) => sent.push({ data, origin }),
  } as unknown as Window;
  const reply = (overrides: Record<string, unknown> = {}) => {
    const event = { source: target, origin: ORIGIN, data: { source: "anteroom-extension", version: 1, requestId: sent[sent.length - 1]?.data.requestId,
      ok: true, state: "connected", extensionVersion: "0.2.38" }, ...overrides } as unknown as MessageEvent;
    for (const listener of listeners) listener(event);
  };
  return { target, sent, timers, listeners, reply };
}

describe("cloud extension transport", () => {
  it("sends only connection identity fields and correlates exact-origin replies", async () => {
    const c = channel(); const pending = cloudExtension(ACCOUNT, c.target).connect();
    expect(c.sent[0].origin).toBe(ORIGIN);
    expect(c.sent[0].data).toMatchObject({ action: "connect", accountId: ACCOUNT.id, email: ACCOUNT.email });
    expect(JSON.stringify(c.sent)).not.toContain("access_token");
    c.reply({ origin: "https://evil.example" });
    c.reply({ source: {} });
    c.reply({ data: { ...c.sent[0].data, source: "anteroom-extension", requestId: "wrong" } });
    expect(c.listeners.size).toBe(1);
    c.reply();
    await expect(pending).resolves.toMatchObject({ state: "connected", ok: true });
    expect(c.listeners.size).toBe(0); expect(c.timers.size).toBe(0);
  });
  it("omits email from routine status/disconnect messages and times out a missing extension", async () => {
    const c = channel(); const bridge = cloudExtension(ACCOUNT, c.target);
    const pending = bridge.status();
    expect(c.sent[0].data).not.toHaveProperty("email");
    for (const timer of c.timers.values()) timer();
    await expect(pending).resolves.toMatchObject({ state: "unavailable", ok: false });
    const disconnected = bridge.disconnect();
    expect(c.sent[c.sent.length - 1]?.data).not.toHaveProperty("email"); c.reply(); await disconnected;
  });
  it("never opens the bridge from an unapproved origin", async () => {
    const c = channel("https://evil.example");
    await expect(cloudExtension(ACCOUNT, c.target).connect()).resolves.toMatchObject({ state: "unavailable" });
    expect(c.sent).toHaveLength(0);
  });
});
