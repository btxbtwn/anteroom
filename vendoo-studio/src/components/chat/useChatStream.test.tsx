// @vitest-environment happy-dom
import { act, useLayoutEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job } from "../../api/types";
import { getLive, patchLive, queueChatGenerate } from "./liveChatStore";
import { isResumableGenerateFailure } from "./chatStream";
import { useChatStream, useGenerateRecovery } from "./useChatStream";

// Reconnect backoff is real time in the app; here it resolves at once and
// records each wait, so a test can see the schedule without sleeping.
const sleeps: number[] = [];
const onSleep: { current: (ms: number) => void } = { current: () => {} };
vi.mock("./chatStream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./chatStream")>();
  return {
    ...actual,
    sleep: async (ms: number) => {
      sleeps.push(ms);
      onSleep.current(ms);
    },
  };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Handler = (url: string, init: RequestInit) => Promise<Response> | Response;
let routes: Handler;
const calls: { url: string; init: RequestInit }[] = [];

function sse(...events: string[]): Response {
  return new Response(events.join("\n") + "\n", { headers: { "Content-Type": "text/event-stream" } });
}

const DONE = "data: [DONE]\n";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function abortError() {
  return Object.assign(new Error("The operation was aborted."), { name: "AbortError" });
}

/** A request that stays open until its signal aborts, like a generate still running. */
function hangUntilAborted(init: RequestInit): Promise<Response> {
  return new Promise((_resolve, reject) => {
    const signal = init.signal;
    if (signal?.aborted) reject(abortError());
    signal?.addEventListener("abort", () => reject(abortError()));
  });
}

/** A response whose body the test feeds, and which errors when the request aborts. */
function openStream(init: RequestInit) {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(c) { controller = c; } });
  init.signal?.addEventListener("abort", () => controller.error(abortError()));
  return {
    response: new Response(body, { headers: { "Content-Type": "text/event-stream" } }),
    push: (text: string) => controller.enqueue(encoder.encode(text)),
    close: () => {
      try {
        controller.close();
      } catch {
        // Already errored by an abort.
      }
    },
  };
}

let convSeq = 0;
let convId = "";
const mounted: Root[] = [];

beforeEach(() => {
  convSeq += 1;
  convId = `conv-${convSeq}`;
  sleeps.length = 0;
  onSleep.current = () => {};
  calls.length = 0;
  routes = (url) => {
    if (url.endsWith("/stop") || url.endsWith("/generate/cancel") || url.includes("/jobs/")) return json({ ok: true });
    throw new Error(`Unexpected request ${url}`);
  };
  vi.stubGlobal("fetch", vi.fn(async (input: string, init: RequestInit = {}) => {
    calls.push({ url: String(input), init });
    return routes(String(input), init);
  }));
});

afterEach(() => {
  act(() => mounted.splice(0).forEach((root) => root.unmount()));
  vi.unstubAllGlobals();
});

type ChatStream = ReturnType<typeof useChatStream>;

function mountChat(overrides: Partial<Parameters<typeof useChatStream>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const setInput = vi.fn();
  const hook: { current: ChatStream | null } = { current: null };
  function Harness() {
    const chat = useChatStream({
      convId,
      setInput,
      pinChatToBottom: () => {},
      activeProbe: undefined,
      failedAction: null,
      lastSendText: "",
      streamText: "",
      ...overrides,
    });
    useLayoutEffect(() => { hook.current = chat; });
    return null;
  }
  const root = createRoot(document.createElement("div"));
  mounted.push(root);
  act(() => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>));
  return { chat: () => hook.current!, setInput, client };
}

const requested = (fragment: string) => calls.filter((call) => call.url.includes(fragment));

describe("useChatStream generate", () => {
  it("streams a generate to completion and folds the turn", async () => {
    routes = (url) => {
      expect(url).toBe(`/api/conversations/${convId}/generate`);
      return sse("event: status", "data: Writing the listing…", "", "data: Vintage denim jacket", "", DONE);
    };
    const { chat } = mountChat();

    await act(() => chat().handleGenerate());

    const live = getLive(convId);
    expect(requested("/generate")).toHaveLength(1);
    expect(requested("/generate")[0].init.method).toBe("POST");
    expect(sleeps).toEqual([]);
    expect(live).toMatchObject({
      streaming: false,
      generating: false,
      controller: null,
      failedAction: null,
    });
    expect(live.foldedTurns).toHaveLength(1);
    expect(live.foldedTurns[0].cancelled).toBe(false);
  });

  // A finished turn clears its live reply after the saved messages refresh.
  it("clears the live reply once the saved messages are refreshed", async () => {
    routes = () => sse("event: status", "data: Writing the listing…", "", "data: Vintage denim jacket", "", DONE);
    const { chat } = mountChat();

    await act(() => chat().handleGenerate());

    expect(getLive(convId)).toMatchObject({ streamText: "", streamStatus: "", thinkingStarted: false });
  });

  it("shows the reply as it streams", async () => {
    let stream!: ReturnType<typeof openStream>;
    routes = (_url, init) => {
      stream = openStream(init);
      return stream.response;
    };
    const { chat } = mountChat();

    let done!: Promise<void>;
    act(() => { done = chat().handleGenerate(); });
    await vi.waitFor(() => expect(stream).toBeDefined());
    expect(getLive(convId)).toMatchObject({ streaming: true, generating: true, streamStatus: "Analyzing photos…" });

    stream.push("data: First words\n\n");
    await vi.waitFor(() => expect(getLive(convId).streamText).toBe("First words"));
    expect(getLive(convId).streaming).toBe(true);

    stream.push(`data:  and more\n\n${DONE}`);
    stream.close();
    await act(() => done);
    expect(getLive(convId)).toMatchObject({ streaming: false, generating: false, failedAction: null });
  });

  it("reattaches with resume when the stream ends before [DONE]", async () => {
    const statuses: string[] = [];
    onSleep.current = () => statuses.push(getLive(convId).streamStatus);
    let attempt = 0;
    routes = () => {
      attempt += 1;
      return attempt === 1
        ? sse("event: status", "data: Identifying category", "", "data: Half a draft", "")
        : sse("data: The whole draft", "", DONE);
    };
    const { chat } = mountChat();

    await act(() => chat().handleGenerate());

    const generates = requested("/generate");
    expect(generates.map((call) => call.url)).toEqual([
      `/api/conversations/${convId}/generate`,
      `/api/conversations/${convId}/generate?resume=1`,
    ]);
    expect(sleeps).toEqual([1500]);
    expect(statuses).toEqual(["Identifying category · reconnecting…"]);
    expect(getLive(convId)).toMatchObject({ streaming: false, generating: false, failedAction: null });
  });

  it("keeps the partial draft on screen while it reconnects", async () => {
    const shown: string[] = [];
    onSleep.current = () => shown.push(getLive(convId).streamText);
    let attempt = 0;
    routes = () => (++attempt === 1 ? sse("data: Half a draft", "") : sse(DONE));
    const { chat } = mountChat();

    await act(() => chat().handleGenerate());

    expect(shown).toEqual(["Half a draft"]);
  });

  it("reattaches after a dropped connection", async () => {
    const shown: { status: string; text: string }[] = [];
    onSleep.current = () => shown.push({ status: getLive(convId).streamStatus, text: getLive(convId).streamText });
    let attempt = 0;
    routes = () => {
      attempt += 1;
      if (attempt === 1) throw new TypeError("Failed to fetch");
      return sse("data: Done after a drop", "", DONE);
    };
    const { chat } = mountChat();

    await act(() => chat().handleGenerate());

    expect(requested("/generate").map((call) => call.url.endsWith("?resume=1"))).toEqual([false, true]);
    expect(shown).toEqual([{ status: "Analyzing photos… · reconnecting…", text: "" }]);
    expect(getLive(convId)).toMatchObject({ streaming: false, failedAction: null });
  });

  it("keeps reconnecting through server errors while the server restarts", async () => {
    let attempt = 0;
    routes = () => {
      attempt += 1;
      if (attempt === 1) return sse("data: Half a draft", "");
      if (attempt === 2) return new Response("Internal Server Error", { status: 500 });
      if (attempt === 3) return new Response("", { status: 502 });
      if (attempt === 4) return json({ detail: "Draft generation is being configured. Try again shortly." }, 503);
      return sse("data: The whole draft", "", DONE);
    };
    const { chat } = mountChat();

    await act(() => chat().handleGenerate());

    expect(requested("/generate").map((call) => call.url.endsWith("?resume=1"))).toEqual([false, true, true, true, true]);
    expect(sleeps).toEqual([1500, 3000, 4500, 5000]);
    expect(getLive(convId)).toMatchObject({ streaming: false, generating: false, failedAction: null });
  });

  it("stops reconnecting on a client error", async () => {
    let attempt = 0;
    routes = () => (++attempt === 1 ? sse("data: Half a draft", "") : json({ detail: "Sign in again" }, 401));
    const { chat } = mountChat();

    await act(() => chat().handleGenerate());

    expect(requested("/generate")).toHaveLength(2);
    expect(getLive(convId).streamText).toMatch(/^Error: Listing generation failed \(HTTP 401\)/);
  });

  it("does not stack the reconnecting suffix across attempts", async () => {
    const statuses: string[] = [];
    onSleep.current = () => statuses.push(getLive(convId).streamStatus);
    let attempt = 0;
    routes = () => {
      attempt += 1;
      if (attempt <= 3) throw new TypeError("Load failed");
      return sse(DONE);
    };
    const { chat } = mountChat();

    await act(() => chat().handleGenerate());

    expect(statuses).toEqual(Array(3).fill("Analyzing photos… · reconnecting…"));
  });

  it("backs off up to five seconds and gives up after twenty attempts", async () => {
    routes = () => { throw new TypeError("Failed to fetch"); };
    const { chat } = mountChat();

    await act(() => chat().handleGenerate());

    expect(requested("/generate")).toHaveLength(20);
    expect(sleeps).toEqual([1500, 3000, 4500, ...Array(16).fill(5000)]);
    const live = getLive(convId);
    expect(live.streamText).toMatch(/^Error: Listing generation connection dropped/);
    expect(live).toMatchObject({ failedAction: "generate", streaming: false, generating: false, controller: null });
    expect(isResumableGenerateFailure(live.streamText)).toBe(true);
  });

  it("reports a generate that never finished and never wrote anything", async () => {
    routes = () => sse(": keepalive", "");
    const { chat } = mountChat();

    await act(() => chat().handleGenerate());

    expect(requested("/generate")).toHaveLength(20);
    const live = getLive(convId);
    expect(live.streamText).toMatch(/^Error: Listing generation did not finish/);
    expect(live.failedAction).toBe("generate");
    expect(live.foldedTurns).toHaveLength(0);
  });

  it("does not retry an HTTP error", async () => {
    routes = () => json({ detail: "Add photos first" }, 422);
    const { chat } = mountChat();

    await act(() => chat().handleGenerate());

    expect(requested("/generate")).toHaveLength(1);
    expect(sleeps).toEqual([]);
    expect(getLive(convId)).toMatchObject({
      streamText: "Error: Listing generation failed (HTTP 422) during “Analyzing photos…”: Add photos first.",
      failedAction: "generate",
      streaming: false,
      generating: false,
      streamTrace: [],
    });
  });

  it("keeps an error the server streamed and does not fold it away", async () => {
    routes = () => sse("event: status", "data: Reading photos", "", "data: Error: Photo analysis failed.", "", DONE);
    const { chat } = mountChat();

    await act(() => chat().handleGenerate());

    const live = getLive(convId);
    expect(live.streamText).toBe("Error: Photo analysis failed.");
    expect(live.failedAction).toBe("generate");
    expect(live.streaming).toBe(false);
    expect(live.foldedTurns).toHaveLength(0);
    expect(live.streamTrace.length).toBeGreaterThan(0);
  });

  it("waits for photo uploads before generating", async () => {
    const { trackPhotoUpload } = await import("../../photoUploads");
    let finish!: () => void;
    const upload = trackPhotoUpload(convId, () => new Promise<void>((resolve) => { finish = resolve; }));
    const { chat } = mountChat();

    await act(() => chat().handleGenerate());

    expect(calls).toHaveLength(0);
    finish();
    await upload;
  });

  it("Stop aborts the run, folds it as cancelled and asks the server to stop", async () => {
    routes = (url, init) => {
      if (url.endsWith("/generate")) {
        const stream = openStream(init);
        stream.push("event: status\ndata: Writing the listing…\n\n");
        return stream.response;
      }
      return json({ ok: true });
    };
    const { chat } = mountChat();

    let generate!: Promise<void>;
    act(() => { generate = chat().handleGenerate(); });
    await vi.waitFor(() => expect(getLive(convId).streamTrace.length).toBeGreaterThan(0));

    await act(() => chat().handleCancel());
    await act(() => generate);

    const live = getLive(convId);
    expect(requested(`/api/conversations/${convId}/stop`)).toHaveLength(1);
    expect(live).toMatchObject({
      streaming: false,
      generating: false,
      controller: null,
      userCancelled: false,
      streamText: "",
      failedAction: null,
    });
    expect(live.foldedTurns[live.foldedTurns.length - 1]?.cancelled).toBe(true);
    expect(chat().stopping).toBe(false);
    // Stop never turns into a reconnect.
    expect(requested("/generate")).toHaveLength(1);
  });

  // A generate has no composer text, so Stop must not restore the last chat message.
  it("Stop on a generate does not paste an earlier chat message into the composer", async () => {
    patchLive(convId, { lastSendText: "make the title shorter" });
    routes = (url, init) => (url.endsWith("/generate") ? hangUntilAborted(init) : json({ ok: true }));
    const { chat, setInput } = mountChat();

    let generate!: Promise<void>;
    act(() => { generate = chat().handleGenerate(); });
    await vi.waitFor(() => expect(requested("/generate")).toHaveLength(1));
    await act(() => chat().handleCancel());
    await act(() => generate);

    expect(setInput).not.toHaveBeenCalledWith("make the title shorter");
  });

  it("a newer stream takes over and the old one leaves the state alone", async () => {
    let attempt = 0;
    let second!: ReturnType<typeof openStream>;
    routes = (_url, init) => {
      attempt += 1;
      if (attempt === 1) return hangUntilAborted(init);
      second = openStream(init);
      return second.response;
    };
    const { chat } = mountChat();

    let first!: Promise<void>;
    act(() => { first = chat().streamFromFetch(`/api/conversations/${convId}/generate`, "First"); });
    await vi.waitFor(() => expect(attempt).toBe(1));
    let next!: Promise<void>;
    act(() => { next = chat().streamFromFetch(`/api/conversations/${convId}/generate`, "Second", true); });
    await act(() => first);

    // The superseded run neither failed nor ended the new one.
    expect(getLive(convId)).toMatchObject({ streaming: true, streamStatus: "Second", failedAction: null });
    expect(calls[1].url.endsWith("?resume=1")).toBe(true);

    second.push(`data: ok\n\n${DONE}`);
    second.close();
    await act(() => next);
    expect(getLive(convId)).toMatchObject({ streaming: false, generating: false, controller: null });
  });

  it("Cancel discovery clears the run and cancels the probe job", async () => {
    let stream!: ReturnType<typeof openStream>;
    routes = (url, init) => {
      if (url.endsWith("/generate")) {
        stream = openStream(init);
        return stream.response;
      }
      // The server ends the run it was asked to cancel.
      if (url.endsWith("/generate/cancel")) stream.close();
      return json({ ok: true });
    };
    const probe = { id: "job-7", conversation_id: convId, mode: "schema_probe", status: "dispatched" } as Job;
    const { chat } = mountChat({ activeProbe: probe });

    let generate!: Promise<void>;
    act(() => { generate = chat().handleGenerate(); });
    await vi.waitFor(() => expect(stream).toBeDefined());
    await act(() => chat().handleCancelDiscovery());
    await act(() => generate);

    expect(requested(`/api/conversations/${convId}/generate/cancel`)).toHaveLength(1);
    expect(requested("/api/jobs/job-7/cancel")).toHaveLength(1);
    expect(getLive(convId)).toMatchObject({
      streaming: false,
      generating: false,
      controller: null,
      streamText: "",
      failedAction: null,
    });
    // No reconnect after a cancel.
    expect(requested("/generate?resume=1")).toHaveLength(0);
  });

  // Cancel discovery must abort the request it clears from the chat.
  it("Cancel discovery aborts the open generate request", async () => {
    let stream!: ReturnType<typeof openStream>;
    routes = (url, init) => {
      if (url.endsWith("/generate")) {
        stream = openStream(init);
        return stream.response;
      }
      return json({ ok: true });
    };
    const { chat } = mountChat();

    let generate!: Promise<void>;
    act(() => { generate = chat().handleGenerate(); });
    await vi.waitFor(() => expect(stream).toBeDefined());
    await act(() => chat().handleCancelDiscovery());
    try {
      expect(requested("/generate")[0].init.signal?.aborted).toBe(true);
    } finally {
      stream.close();
      await act(() => generate);
    }
  });

  it("Retry after a dropped connection reattaches instead of cancelling discovery", async () => {
    let attempt = 0;
    routes = (url) => {
      if (!url.includes("/generate")) return json({ ok: true });
      attempt += 1;
      return sse(DONE);
    };
    patchLive(convId, { streamText: "Error: Listing generation connection dropped." });
    const probe = { id: "job-8", conversation_id: convId, mode: "schema_probe", status: "dispatched" } as Job;
    const { chat } = mountChat({ activeProbe: probe, failedAction: "generate" });

    await act(() => chat().handleRetry());
    await vi.waitFor(() => expect(getLive(convId).streaming).toBe(false));

    expect(attempt).toBe(1);
    expect(requested("/generate/cancel")).toHaveLength(0);
    expect(requested("/jobs/")).toHaveLength(0);
  });
});

describe("useChatStream send", () => {
  it("posts the message, clears the composer and streams the reply", async () => {
    routes = (url, init) => {
      expect(url).toBe(`/api/conversations/${convId}/messages`);
      expect(JSON.parse(String(init.body))).toEqual({ text: "Lower the price" });
      return sse("data: Done, $20 now.", "", DONE);
    };
    const { chat, setInput } = mountChat();

    let sent: boolean | undefined;
    await act(async () => { sent = await chat().sendMessage("Lower the price"); });

    expect(sent).toBe(true);
    expect(setInput).toHaveBeenCalledWith("");
    expect(getLive(convId)).toMatchObject({
      streaming: false,
      controller: null,
      failedAction: null,
      lastSendText: "Lower the price",
    });
  });

  it("refuses to send while a generate is running", async () => {
    patchLive(convId, { generating: true });
    const { chat, setInput } = mountChat();

    let sent: boolean | undefined;
    await act(async () => { sent = await chat().sendMessage("hello"); });

    expect(sent).toBe(false);
    expect(calls).toHaveLength(0);
    expect(setInput).not.toHaveBeenCalled();
    patchLive(convId, { generating: false });
  });

  it("refuses an empty message", async () => {
    const { chat } = mountChat();
    let sent: boolean | undefined;
    await act(async () => { sent = await chat().sendMessage(""); });
    expect(sent).toBe(false);
    expect(calls).toHaveLength(0);
  });

  it("marks a failed send for retry and does not reconnect", async () => {
    routes = () => json({ detail: "Provider is not set up" }, 400);
    const { chat } = mountChat();

    await act(async () => { await chat().sendMessage("hi"); });

    expect(calls).toHaveLength(1);
    expect(getLive(convId)).toMatchObject({
      failedAction: "send",
      streaming: false,
      controller: null,
      streamText: "Error: Chat request failed (HTTP 400): Provider is not set up.",
    });
  });

  it("marks a dropped send for retry", async () => {
    routes = () => { throw new TypeError("Failed to fetch"); };
    const { chat } = mountChat();

    await act(async () => { await chat().sendMessage("hi"); });

    expect(calls).toHaveLength(1);
    expect(sleeps).toEqual([]);
    expect(getLive(convId).streamText).toMatch(/^Error: Chat request connection dropped/);
    expect(getLive(convId).failedAction).toBe("send");
  });

  it("Stop puts the unanswered message back in the composer", async () => {
    routes = (url, init) => (url.endsWith("/messages") ? hangUntilAborted(init) : json({ ok: true }));
    const { chat, setInput } = mountChat();

    let send!: Promise<boolean>;
    act(() => { send = chat().sendMessage("Add measurements"); });
    await vi.waitFor(() => expect(requested("/messages")).toHaveLength(1));
    await act(() => chat().handleCancel());
    await act(() => send);

    expect(setInput.mock.calls.map(([value]) => value)).toEqual(["", "Add measurements"]);
    expect(getLive(convId)).toMatchObject({ streaming: false, controller: null, userCancelled: false });
  });

  it("Retry resends the failed message", async () => {
    routes = () => sse("data: ok", "", DONE);
    const { chat } = mountChat({ failedAction: "send", lastSendText: "Try again please" });

    await act(() => chat().handleRetry());
    await vi.waitFor(() => expect(getLive(convId).streaming).toBe(false));

    expect(requested("/messages")).toHaveLength(1);
    expect(JSON.parse(String(requested("/messages")[0].init.body))).toEqual({ text: "Try again please" });
  });
});

describe("useGenerateRecovery", () => {
  let hidden = false;
  beforeEach(() => {
    hidden = false;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  });

  function mountRecovery(hasListingJson = false) {
    const streamFromFetch = vi.fn(async () => {});
    function Harness({ json }: { json: boolean }) {
      useGenerateRecovery(convId, getLive(convId).generating, json, streamFromFetch);
      return null;
    }
    const root = createRoot(document.createElement("div"));
    mounted.push(root);
    act(() => root.render(<Harness json={hasListingJson} />));
    return { streamFromFetch, rerender: (json: boolean) => act(() => root.render(<Harness json={json} />)) };
  }

  it("reattaches to a generate the server is still running", () => {
    patchLive(convId, { generating: true, streamStatus: "Identifying category" });
    const { streamFromFetch } = mountRecovery();
    expect(streamFromFetch).toHaveBeenCalledWith(`/api/conversations/${convId}/generate`, "Identifying category", true);
  });

  it("starts a queued generate instead of resuming one", () => {
    queueChatGenerate(convId);
    const { streamFromFetch } = mountRecovery();
    expect(streamFromFetch).toHaveBeenCalledWith(`/api/conversations/${convId}/generate`, "Analyzing photos…", false);
    expect(getLive(convId).startQueued).toBe(false);
  });

  it("leaves an idle, attached or cancelled chat alone", () => {
    expect(mountRecovery().streamFromFetch).not.toHaveBeenCalled();
    patchLive(convId, { generating: true, userCancelled: true });
    expect(mountRecovery().streamFromFetch).not.toHaveBeenCalled();
    patchLive(convId, { userCancelled: false, controller: new AbortController() });
    expect(mountRecovery().streamFromFetch).not.toHaveBeenCalled();
    patchLive(convId, { generating: false, controller: null });
  });

  it("waits until the page is visible again", () => {
    hidden = true;
    patchLive(convId, { generating: true });
    const { streamFromFetch } = mountRecovery();
    expect(streamFromFetch).not.toHaveBeenCalled();

    hidden = false;
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(streamFromFetch).toHaveBeenCalledTimes(1);
  });

  it("settles a generating flag once the listing has arrived", () => {
    patchLive(convId, { generating: true, streaming: true, streamStatus: "Writing", userCancelled: true });
    const { rerender } = mountRecovery(false);
    expect(getLive(convId).generating).toBe(true);
    rerender(true);
    expect(getLive(convId)).toMatchObject({ generating: false, streaming: false, streamStatus: "" });
  });
});
