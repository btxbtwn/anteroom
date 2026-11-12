import { afterEach, expect, it, vi } from "vitest";
import { connectorBridge, nativeClaudeTerminal, type ConnectorBridge } from "./connectorBridge";

afterEach(() => vi.unstubAllGlobals());

it("detects the connector separately from the full desktop bridge", () => {
  vi.stubGlobal("window", { pywebview: { api: { connect_chatgpt_worker: vi.fn() } } });
  expect(connectorBridge()).toBeNull();
  const api = { connector_status: vi.fn() };
  vi.stubGlobal("window", { pywebview: { api } });
  expect(connectorBridge()).toBe(api);
});

it("closes a sign-in that finishes starting after its terminal was dismissed", async () => {
  let started!: (value: { id: string }) => void;
  const close = vi.fn().mockResolvedValue({ ok: true });
  const bridge = {
    connector_claude_start: () => new Promise<{ id: string }>(resolve => { started = resolve; }),
    connector_claude_close: close,
  } as unknown as ConnectorBridge;
  const terminal = nativeClaudeTerminal(bridge)(80, 24, { message: vi.fn(), close: vi.fn() });
  terminal.close();
  started({ id: "one-sign-in" });
  await vi.waitFor(() => expect(close).toHaveBeenCalledWith("one-sign-in"));
});

it("passes only terminal input and frames through the native bridge", async () => {
  const frames = [{ data: [65, 66] }, { code: 0 }];
  const message = vi.fn();
  const send = vi.fn().mockResolvedValue({ ok: true });
  const close = vi.fn().mockResolvedValue({ ok: true });
  const bridge = {
    connector_claude_start: async () => ({ id: "one-sign-in" }),
    connector_claude_read: async () => frames.shift(),
    connector_claude_send: send,
    connector_claude_close: close,
  } as unknown as ConnectorBridge;
  const terminal = nativeClaudeTerminal(bridge)(80, 24, { message, close: vi.fn() });
  await vi.waitFor(() => expect(message).toHaveBeenCalledTimes(2));
  expect(message.mock.calls[0][0]).toEqual(new Uint8Array([65, 66]));
  expect(JSON.parse(message.mock.calls[1][0])).toEqual({ type: "exit", code: 0 });
  terminal.send({ type: "input", data: "test-code\n" });
  expect(send).toHaveBeenCalledWith("one-sign-in", { type: "input", data: "test-code\n" });
  terminal.close();
  expect(close).toHaveBeenCalledWith("one-sign-in");
});
