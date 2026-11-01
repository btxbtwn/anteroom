import { describe, expect, it } from "vitest";
import { cloudExtension } from "./cloudExtension";
const ORIGIN = "https://myanteroom.com";
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

describe("Poshmark form capability", () => {
  it("requires an approved connection and a reader capability before sending listing data", async () => {
    const c = channel(); const bridge = cloudExtension(ACCOUNT, c.target);
    const pending = bridge.readPoshmarkForm({ title: "Coat" });
    c.reply();
    await expect(pending).rejects.toThrow("0.2.39");
    expect(c.sent).toHaveLength(1);
    expect(c.sent[0].data).not.toHaveProperty("listing");
  });
  it("never retries reads, excludes the email/token and keeps diagnostics out of errors", async () => {
    const c = channel(); const bridge = cloudExtension(ACCOUNT, c.target);
    const pending = bridge.readPoshmarkForm({ title: "Coat", price: 42 });
    c.reply({ data: { source: "anteroom-extension", version: 1, requestId: c.sent[0].data.requestId, ok: true, state: "connected", supportedActions: ["poshmark.form.read"] } });
    // Let status() finish and the explicit reader request be posted.
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(c.sent[1].data).toMatchObject({ action: "poshmark_form", listing: { title: "Coat", price: 42 } });
    expect(c.sent[1].data).not.toHaveProperty("email");
    expect(JSON.stringify(c.sent)).not.toContain("access_token");
    c.reply({ data: { source: "anteroom-extension", version: 1, requestId: c.sent[1].data.requestId, ok: false, error_code: "busy", error: "PRIVATE diagnostic" } });
    await expect(pending).rejects.toThrow("Chrome is busy");
    expect(c.sent).toHaveLength(2);
  });
});

describe("Save draft relay", () => {
  const command = { body: "eyJ2IjoxfQ", signature: "s".repeat(86) };
  it("relays the signed command unread, without session fields, and returns once Anteroom has the result", async () => {
    const c = channel(); const bridge = cloudExtension(ACCOUNT, c.target);
    const pending = bridge.saveDraft({ ...command, extra: "dropped" } as typeof command);
    expect(c.sent[0].data).toMatchObject({ action: "marketplace_save_draft", accountId: ACCOUNT.id, command });
    expect(Object.keys(c.sent[0].data.command as object)).toEqual(["body", "signature"]);
    expect(c.sent[0].data).not.toHaveProperty("email");
    expect(JSON.stringify(c.sent)).not.toContain("access_token");
    c.reply({ data: { source: "anteroom-extension", version: 1, requestId: c.sent[0].data.requestId, ok: true, recorded: true } });
    await expect(pending).resolves.toEqual({ ok: true, recorded: true });
  });
  it("says what to do when Chrome refused or could not report", async () => {
    for (const [reply, message] of [
      [{ ok: false, recorded: false, error_code: "not_connected" }, "Connect Chrome to save drafts"],
      [{ ok: false, recorded: false, error_code: "invalid_command", error: "PRIVATE" }, "Update Anteroom Bridge"],
      [{ ok: true, recorded: false }, "could not tell Anteroom"],
    ] as const) {
      const c = channel(); const pending = cloudExtension(ACCOUNT, c.target).saveDraft(command);
      c.reply({ data: { source: "anteroom-extension", version: 1, requestId: c.sent[0].data.requestId, ...reply } });
      await expect(pending).rejects.toThrow(message);
    }
    // A refusal Chrome reported is on the listing already: no error here.
    const c = channel(); const pending = cloudExtension(ACCOUNT, c.target).saveDraft(command);
    c.reply({ data: { source: "anteroom-extension", version: 1, requestId: c.sent[0].data.requestId, ok: false, recorded: true, error_code: "signed_out" } });
    await expect(pending).resolves.toMatchObject({ ok: false, recorded: true });
  });
  it("keeps waiting for a long save, then says Chrome is still working", async () => {
    const c = channel(); const pending = cloudExtension(ACCOUNT, c.target).saveDraft(command);
    for (const timer of c.timers.values()) timer();
    await expect(pending).rejects.toThrow("still working");
  });
  it("reports the Save draft capability alongside the form reader", async () => {
    const c = channel(); const pending = cloudExtension(ACCOUNT, c.target).status();
    c.reply({ data: { source: "anteroom-extension", version: 1, requestId: c.sent[0].data.requestId, ok: true, state: "connected",
      supportedActions: ["marketplace.save_draft", "poshmark.form.read", "marketplace.list"] } });
    await expect(pending).resolves.toMatchObject({ supportedActions: ["marketplace.save_draft", "poshmark.form.read"] });
  });
});
