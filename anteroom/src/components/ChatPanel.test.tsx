// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ComponentProps, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatPanel } from "./ChatPanel";
import type { ChatComposer } from "./chat/ChatComposer";
import { getComposerDraft, resetComposerDrafts, setComposerDraftImages, setComposerDraftText } from "./chat/composerDrafts";

import * as composerImages from "./chat/composerImages";
import { chatQueue, enqueueChatMessage, newQueuedChatMessage, removeChatMessage } from "./chat/chatQueueStore";
import { getLive, patchLive, resetChatLive } from "./chat/liveChatStore";

import type { ConversationActivity, Message } from "../api/types";

const { uploadPhotos, handleGenerate, sendMessage, serverState } = vi.hoisted(() => ({
  uploadPhotos: vi.fn(),
  handleGenerate: vi.fn(),
  sendMessage: vi.fn(),
  serverState: {
    messages: [] as Message[],
    activity: undefined as ConversationActivity | undefined,
  },
}));

vi.mock("../api/client", () => ({ api: { conversations: { uploadListingPhoto: uploadPhotos } } }));
vi.mock("./ChatMarkdown", () => ({
  CHAT_PROSE_CLASS: "",
  ChatMarkdown: ({ text }: { text: string }) => <div data-chat-markdown>{text}</div>,
}));
vi.mock("./chat/useChatServerState", () => ({
  useChatServerState: (_convId: string, busy: boolean) => ({
    photos: uploadPhotos.mock.calls.length ? [{ id: "photo-1" }] : [],
    messages: serverState.messages,
    activity: serverState.activity,
    busy: busy || Boolean(serverState.activity?.busy),
  }),
}));
vi.mock("./chat/useChatStream", () => ({
  useGenerateRecovery: () => {},
  useChatStream: () => ({ handleGenerate, sendMessage }),
  canSteerLive: () => false,
  steerableChat: () => false,
}));
vi.mock("./chat/ChatTranscript", () => ({
  ChatTranscript: ({ children }: { children: ReactNode }) => <>{children}</>,
}));
vi.mock("./chat/ChatComposer", () => ({
  ChatComposer: ({ images, onSend, onEditQueued }: ComponentProps<typeof ChatComposer>) => (
    <>
      <button data-edit onClick={onEditQueued}>Edit</button>
      <button onClick={() => onSend(false)}>Send</button>
      {images.error && <p role="alert">{images.error}</p>}
    </>
  ),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("listing photo uploads from chat", () => {
  const draftKey = "listing:listing-a";
  let root: Root;
  let container: HTMLDivElement;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.resetAllMocks();
    serverState.messages = [];
    serverState.activity = undefined;
    resetComposerDrafts();
    resetChatLive("listing-a");
    resetChatLive("listing-b");
    sendMessage.mockResolvedValue(true);
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    queryClient = new QueryClient();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.unstubAllGlobals();
  });

  async function renderChat(convId = "listing-a", queuedMessage?: string) {
    await act(async () => root.render(
      <QueryClientProvider client={queryClient}>
        <ChatPanel key={convId} convId={convId} queuedMessage={queuedMessage} />
      </QueryClientProvider>,
    ));
  }

  async function send() {
    const button = [...container.querySelectorAll("button")].find((button) => button.textContent === "Send");
    expect(button).toBeDefined();
    await act(async () => button?.click());
  }

  it("preserves edited text when another listing panel removes the queued item", async () => {
    const item = newQueuedChatMessage("original");
    enqueueChatMessage(draftKey, item);
    patchLive("listing-a", { streaming: true });
    await renderChat();
    await act(async () => container.querySelector<HTMLButtonElement>("[data-edit]")!.click());
    expect(getComposerDraft(draftKey).text).toBe("original");
    await act(async () => {
      removeChatMessage(draftKey, item.id);
      setComposerDraftText(draftKey, "keep my edit");
    });
    await send();
    expect(getComposerDraft(draftKey).text).toBe("keep my edit");
    expect(chatQueue(draftKey)).toEqual([]);
  });

  it("prepares a busy draft only once and leaves it intact for retry after failure", async () => {
    await uploadPhotos(); // The server-state fixture now reports an existing listing photo.
    patchLive("listing-a", { streaming: true });
    const attachment = { id: "tag", file: new File(["tag"], "tag.jpg", { type: "image/jpeg" }) };
    setComposerDraftImages(draftKey, [attachment]);
    setComposerDraftText(draftKey, "check tag");
    const first = deferred<composerImages.PreparedImage>();
    const prepare = vi.spyOn(composerImages, "prepareComposerImage").mockReturnValueOnce(first.promise)
      .mockResolvedValue({ ok: true, name: "tag.jpg", dataUrl: "data:image/jpeg;base64,AQ==" });
    try {
      await renderChat();
      await send();
      await send();
      expect(prepare).toHaveBeenCalledTimes(1);
      await act(async () => { first.resolve({ ok: false, reason: "unreadable" }); });
      expect(getComposerDraft(draftKey).images).toEqual([attachment]);
      expect(getComposerDraft(draftKey).text).toBe("check tag");
      await send();
      expect(chatQueue(draftKey).map(item => item.text)).toEqual(["check tag"]);
    } finally {
      prepare.mockRestore();
      await act(async () => { for (const item of chatQueue(draftKey)) removeChatMessage(draftKey, item.id); });
    }
  });

  it.each([false, true])("retries a partial upload after reopening chat (replaced: %s)", async (replace) => {
    const front = new File(["front"], "front.jpg", { type: "image/jpeg" });
    const tag = new File(["tag"], "tag.jpg", { type: "image/jpeg" });
    const correctedTag = new File(["corrected tag"], "tag.jpg", { type: "image/jpeg" });
    setComposerDraftImages(draftKey, [{ id: "front", file: front }, { id: "tag", file: tag }]);
    uploadPhotos
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("Try tag.jpg again."))
      .mockResolvedValueOnce(undefined);

    await renderChat();
    await send();

    expect(uploadPhotos).toHaveBeenNthCalledWith(1, "listing-a", front);
    expect(uploadPhotos).toHaveBeenNthCalledWith(2, "listing-a", tag);
    expect(container.querySelector('[role="alert"]')?.textContent).toBe("Try tag.jpg again.");
    expect(getComposerDraft(draftKey).images.map((image) => image.id)).toEqual(["tag"]);
    expect(handleGenerate).not.toHaveBeenCalled();
    expect(sendMessage).not.toHaveBeenCalled();

    await renderChat("listing-b");
    if (replace) {
      setComposerDraftImages(draftKey, []);
      setComposerDraftImages(draftKey, [{ id: "corrected", file: correctedTag }]);
    }
    await renderChat();
    await send();

    expect(uploadPhotos).toHaveBeenNthCalledWith(3, "listing-a", replace ? correctedTag : tag);
    expect(uploadPhotos).toHaveBeenCalledTimes(3);
    expect(getComposerDraft(draftKey).images).toEqual([]);
    expect(handleGenerate).toHaveBeenCalledTimes(1);
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("does not start generation when saved listing copy loads during the photo upload", async () => {
    const front = new File(["front"], "front.jpg", { type: "image/jpeg" });
    setComposerDraftImages(draftKey, [{ id: "front", file: front }]);
    uploadPhotos.mockImplementationOnce(async () => {
      queryClient.setQueryData(["listing", "listing-a"], {
        conversation_id: "listing-a",
        listing: { title: "Existing Wool Coat", brand: "Pendleton", price: 120 },
      });
    });

    await renderChat();
    await send();

    expect(uploadPhotos).toHaveBeenCalledTimes(1);
    expect(getComposerDraft(draftKey).images).toEqual([]);
    expect(handleGenerate).not.toHaveBeenCalled();
    expect(sendMessage).not.toHaveBeenCalled();
  });
  async function startUpload(note = "") {
    const file = new File(["front"], "front.jpg", { type: "image/jpeg" });
    setComposerDraftImages(draftKey, [{ id: "front", file }]);
    setComposerDraftText(draftKey, note);
    const upload = deferred<void>();
    uploadPhotos.mockReturnValueOnce(upload.promise);
    await renderChat();
    await send();
    return upload;
  }

  function startInspectorMessage() {
    const inspector = deferred<boolean>();
    sendMessage.mockImplementationOnce(() => {
      patchLive("listing-a", { streaming: true, controller: new AbortController() });
      return inspector.promise;
    });
    return async () => {
      await act(async () => {
        patchLive("listing-a", { streaming: false, controller: null });
        inspector.resolve(true);
      });
    };
  }

  it("holds the submitted note through an inspector reply and preserves a newer draft", async () => {
    const upload = await startUpload("  Submitted note  ");
    const finishInspector = startInspectorMessage();
    await renderChat("listing-a", "Inspector prompt");
    const controller = getLive("listing-a").controller;
    expect(controller).not.toBeNull();
    await act(async () => {
      setComposerDraftText(draftKey, "Newer text");
      upload.resolve();
    });
    expect(sendMessage.mock.calls.map(([text]) => text)).toEqual(["Inspector prompt"]);
    expect(getComposerDraft(draftKey).text).toBe("Newer text");

    await finishInspector();
    expect(sendMessage).toHaveBeenLastCalledWith("Submitted note", { preserveInput: true });
    expect(sendMessage).toHaveBeenCalledTimes(2);
    expect(getComposerDraft(draftKey).text).toBe("Newer text");
  });

  it("clears the submitted draft text when handing the note off for dispatch", async () => {
    const upload = await startUpload("  Submitted note  ");
    await act(async () => upload.resolve());
    expect(getComposerDraft(draftKey).text).toBe("");
    expect(sendMessage).toHaveBeenCalledWith("Submitted note", { preserveInput: true });
  });

  it.each([false, true])("waits for an inspector reply before deciding to generate (copy saved: %s)", async (copySaved) => {
    const upload = await startUpload();
    const finishInspector = startInspectorMessage();
    await renderChat("listing-a", "Inspector prompt");
    const controller = getLive("listing-a").controller;
    expect(controller).not.toBeNull();
    await act(async () => upload.resolve());
    expect(handleGenerate).not.toHaveBeenCalled();
    expect(controller?.signal.aborted).toBe(false);

    if (copySaved) {
      queryClient.setQueryData(["listing", "listing-a"], {
        listing: { title: "Existing Wool Coat", brand: "Pendleton", price: 120 },
      });
    }
    await finishInspector();
    expect(handleGenerate).toHaveBeenCalledTimes(copySaved ? 0 : 1);
  });

  function installFrames(reducedMotion = false) {
    let now = 1000;
    let nextId = 1;
    const frames = new Map<number, FrameRequestCallback>();
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: reducedMotion && query.includes("prefers-reduced-motion"),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }) as unknown as MediaQueryList);
    vi.stubGlobal("performance", { ...performance, now: () => now });
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      const id = nextId++;
      frames.set(id, cb);
      return id;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => {
      frames.delete(id);
    });
    return {
      step(ms: number) {
        now += ms;
        const queued = [...frames.entries()];
        frames.clear();
        for (const [, cb] of queued) cb(now);
      },
      pending: () => frames.size,
    };
  }

  function assistantRows() {
    return [...container.querySelectorAll<HTMLElement>('[data-message-role="assistant"]')];
  }

  function assistantMarkdown() {
    return assistantRows().map((row) => row.querySelector("[data-chat-markdown]")?.textContent ?? "");
  }

  it("hides the streaming row when idle, server-busy only, pre-chunk, resetting, failed, or JSON-only", async () => {
    installFrames();
    await renderChat();
    expect(assistantRows()).toHaveLength(0);

    serverState.activity = { busy: true, writing: false, items: ["Checking eBay"], message_count: 0, last_message_id: null };
    await renderChat();
    expect(assistantRows()).toHaveLength(0);

    serverState.activity = undefined;
    await act(async () => patchLive("listing-a", { streaming: true, streamText: "", streamDisplayText: "" }));
    expect(assistantRows()).toHaveLength(0);

    const hiddenJson = '```json\n{"title": "Wool Coat", "price": 120}';
    await act(async () => patchLive("listing-a", { streaming: true, streamText: hiddenJson, streamDisplayText: hiddenJson }));
    expect(assistantRows()).toHaveLength(0);

    await act(async () => patchLive("listing-a", { streaming: true, resetting: true, streamText: "", streamDisplayText: "" }));
    expect(assistantRows()).toHaveLength(0);

    await act(async () => patchLive("listing-a", { streaming: false, resetting: false, streamText: "Error: timed out", streamDisplayText: "Error: timed out" }));
    expect(assistantRows()).toHaveLength(0);
  });

  it("paces the first chunk from zero, clears on cancel, flushes on completion, and avoids duplicate bubbles once saved", async () => {
    const frames = installFrames();
    const reply = "I checked your recent jacket sales across every marketplace and found a steady rise in wool outerwear demand over the past month, with vintage chore coats selling fastest between ninety and one hundred forty dollars.";

    await renderChat();
    await act(async () => patchLive("listing-a", { streaming: true, streamText: "", streamDisplayText: "" }));
    expect(assistantRows()).toHaveLength(0);

    // Chunk 1 arrives while the streaming row is already mounted at 0: it paces rather than dumping all at once.
    await act(async () => patchLive("listing-a", { streaming: true, streamText: reply, streamDisplayText: reply }));
    expect(assistantRows()).toHaveLength(0);
    await act(async () => frames.step(50));
    expect(assistantRows()).toHaveLength(1);
    const firstFrame = assistantMarkdown()[0];
    expect(firstFrame.length).toBeGreaterThan(0);
    expect(firstFrame.length).toBeLessThan(reply.length);

    // Cancel clears the bubble immediately.
    await act(async () => patchLive("listing-a", { streaming: false, streamText: "", streamDisplayText: "" }));
    expect(assistantRows()).toHaveLength(0);

    // Second turn: finish stream before server messages refetch, then flush to exact prose and hand off cleanly.
    await act(async () => patchLive("listing-a", { streaming: true, streamText: "", streamDisplayText: "" }));
    await act(async () => patchLive("listing-a", { streaming: true, streamText: reply, streamDisplayText: reply }));
    await act(async () => frames.step(50));
    expect(assistantMarkdown()[0].length).toBeLessThan(reply.length);

    await act(async () => patchLive("listing-a", { streaming: false, streamText: reply, streamDisplayText: reply }));
    for (let i = 0; i < 60 && frames.pending() > 0; i++) {
      await act(async () => frames.step(50));
    }
    expect(assistantMarkdown()).toEqual([reply]);
    expect(assistantRows()[0].getAttribute("aria-busy")).toBe("true");

    // Once the saved assistant message arrives from the server, only one row remains (no duplicate bubble).
    serverState.messages = [{
      id: "m-1",
      conversation_id: "listing-a",
      role: "assistant",
      text: reply,
      provider: "sandbox",
      model: "sandbox",
      created_at: "2026-11-27T12:00:00Z",
    }];
    await renderChat();
    expect(assistantRows()).toHaveLength(1);
    expect(assistantRows()[0].getAttribute("aria-busy")).toBeNull();
    expect(assistantMarkdown()).toEqual([reply]);
  });

  it("reveals the first chunk immediately under reduced motion", async () => {
    const frames = installFrames(true);
    const reply = "Reduced motion shows the whole first chunk right away with no pacing frames.";

    await renderChat();
    await act(async () => patchLive("listing-a", { streaming: true, streamText: "", streamDisplayText: "" }));
    expect(assistantRows()).toHaveLength(0);

    await act(async () => patchLive("listing-a", { streaming: false, streamText: reply, streamDisplayText: reply }));
    expect(frames.pending()).toBe(0);
    expect(assistantMarkdown()).toEqual([reply]);
  });

  it("resets streaming row state when switching convId in place into an already-streaming listing", async () => {
    installFrames();
    const alreadyArrived = "Already streamed words for the second listing should appear immediately when switching panes.";
    patchLive("listing-a", { streaming: true, streamText: "", streamDisplayText: "" });
    patchLive("listing-b", { streaming: true, streamText: alreadyArrived, streamDisplayText: alreadyArrived });

    // Render without keying ChatPanel itself (matches ListingPanes) so StreamingAssistantRow's own key={convId} is exercised.
    await act(async () => root.render(
      <QueryClientProvider client={queryClient}>
        <ChatPanel convId="listing-a" />
      </QueryClientProvider>,
    ));
    expect(assistantRows()).toHaveLength(0);

    await act(async () => root.render(
      <QueryClientProvider client={queryClient}>
        <ChatPanel convId="listing-b" />
      </QueryClientProvider>,
    ));
    expect(assistantRows()).toHaveLength(1);
    // Streaming display holds back only the trailing unfinished word until complete, not the earlier words.
    expect(assistantMarkdown()[0]).toContain("Already streamed words for the second listing");
  });

});
