// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { chatQueue, enqueueChatMessage, newQueuedChatMessage, removeChatMessage } from "./chat/chatQueueStore";
import { AssistantPage } from "./AssistantPage";
import {
  getComposerDraft,
  resetComposerDrafts,
  setComposerDraftImages,
  setComposerDraftText,
} from "./chat/composerDrafts";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const mocks = vi.hoisted(() => ({
  createThread: vi.fn(),
  ask: vi.fn(),
  resume: vi.fn(),
  retry: vi.fn(),
  stop: vi.fn(),
  prepareComposerImage: vi.fn(),
  addToast: vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: [], isLoading: false, isError: false, isFetching: false, error: null }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useQueryClient: () => ({
    invalidateQueries: vi.fn(() => Promise.resolve()),
    getQueryData: vi.fn(() => []),
    setQueryData: vi.fn(),
  }),
}));

vi.mock("../api/client", () => ({
  api: {
    assistant: {
      messages: vi.fn(() => Promise.resolve([])),
      threads: vi.fn(() => Promise.resolve([])),
      createThread: mocks.createThread,
      ask: mocks.ask,
      resume: mocks.resume,
      retry: mocks.retry,
      stop: mocks.stop,
    },
  },
}));

vi.mock("./chat/composerImages", async () => {
  const actual = await vi.importActual<typeof import("./chat/composerImages")>("./chat/composerImages");
  return {
    ...actual,
    prepareComposerImage: mocks.prepareComposerImage,
  };
});

vi.mock("../ui/toast", () => ({
  addToast: mocks.addToast,
}));

vi.mock("./ChatMarkdown", () => ({
  CHAT_PROSE_CLASS: "",
  ChatMarkdown: ({ text }: { text: string }) => <p>{text}</p>,
}));

vi.mock("./chat/ChatComposer", () => ({
  ChatComposer: ({ onSend, onEditQueued }: { onSend: (alternate: boolean) => void; onEditQueued: () => boolean }) => (
    <>
      <button type="button" data-edit onClick={onEditQueued}>Edit</button>
      <button type="button" data-testid="send-button" onClick={() => onSend(false)}>Send</button>
    </>
  ),
}));

async function flushMicrotasks(turns = 6): Promise<void> {
  for (let i = 0; i < turns; i += 1) {
    await Promise.resolve();
  }
}

describe("AssistantPage concurrency and background failure recovery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetComposerDrafts();
    mocks.resume.mockResolvedValue({});
  });

  afterEach(() => {
    resetComposerDrafts();
  });

  it("sends a saved follow-up after an empty resume", async () => {
    const root = createRoot(document.createElement("div"));
    enqueueChatMessage("assistant", newQueuedChatMessage("saved", { threadId: "idle" }));
    mocks.ask.mockResolvedValue({});
    try {
      await act(async () => {
        root.render(<AssistantPage threadId="idle" onOpenProviders={vi.fn()} onOpenListing={vi.fn()} />);
      });
      expect(mocks.ask).toHaveBeenCalledWith("saved", expect.anything(), expect.anything(), [], "idle");
    } finally {
      await act(async () => root.unmount());
    }
  });

  it.each(["switch", "unmount"])("discards only the temporary edit on %s", async (action) => {
    const container = document.createElement("div");
    const root = createRoot(container);
    const resume = deferred<object>();
    mocks.resume.mockReturnValueOnce(resume.promise);
    const item = newQueuedChatMessage("original", { threadId: "leaving" });
    enqueueChatMessage("assistant", item);
    const page = (threadId: string) => <AssistantPage threadId={threadId} onOpenProviders={vi.fn()} onOpenListing={vi.fn()} />;
    try {
      await act(async () => root.render(page("leaving")));
      await act(async () => container.querySelector<HTMLButtonElement>("[data-edit]")!.click());
      expect(getComposerDraft("assistant:leaving").text).toBe("original");
      await act(async () => setComposerDraftText("assistant:leaving", "unfinished"));
      await act(async () => root.render(action === "switch" ? page("elsewhere") : null));
      expect(getComposerDraft("assistant:leaving").text).toBe("");
      expect(chatQueue("assistant")).toContainEqual(item);
    } finally {
      await act(async () => { removeChatMessage("assistant", item.id); resume.resolve({}); root.unmount(); });
    }
  });

  it("keeps the draft if the queued question disappears during editing", async () => {
    const container = document.createElement("div");
    const root = createRoot(container);
    const resume = deferred<object>();
    mocks.resume.mockReturnValueOnce(resume.promise);
    const item = newQueuedChatMessage("original", { threadId: "edit" });
    enqueueChatMessage("assistant", item);
    try {
      await act(async () => root.render(<AssistantPage threadId="edit" onOpenProviders={vi.fn()} onOpenListing={vi.fn()} />));
      await act(async () => container.querySelector<HTMLButtonElement>("[data-edit]")!.click());
      expect(getComposerDraft("assistant:edit").text).toBe("original");
      await act(async () => {
        setComposerDraftText("assistant:edit", "keep this edit");
        removeChatMessage("assistant", item.id);
      });
      await act(async () => container.querySelector<HTMLButtonElement>('[data-testid="send-button"]')!.click());
      expect(getComposerDraft("assistant:edit").text).toBe("keep this edit");
      expect(mocks.addToast).toHaveBeenCalledWith(expect.objectContaining({ type: "warning" }));
    } finally {
      await act(async () => { resume.resolve({}); root.unmount(); });
    }
  });

  it("steers a resume that becomes live during preparation and waits for Stop before draining", async () => {
    const resume = deferred<object>();
    const stop = deferred<object>();
    const prepare = deferred<{ ok: true; name: string; dataUrl: string }>();
    mocks.resume.mockReturnValueOnce(resume.promise);
    mocks.stop.mockReturnValueOnce(stop.promise);
    mocks.ask.mockResolvedValueOnce({});
    mocks.prepareComposerImage.mockReturnValueOnce(prepare.promise);
    const container = document.createElement("div");
    const root = createRoot(container);
    setComposerDraftImages("assistant:prepare", [{ id: "img", file: new File(["tag"], "tag.png", { type: "image/png" }) }]);
    try {
      await act(async () => root.render(<AssistantPage threadId="prepare" onOpenProviders={vi.fn()} onOpenListing={vi.fn()} />));
      await act(async () => container.querySelector<HTMLButtonElement>('[data-testid="send-button"]')!.click());
      await act(async () => mocks.resume.mock.calls[0][0].onEvent("status", "Working"));
      await act(async () => prepare.resolve({ ok: true, name: "tag.png", dataUrl: "data:image/png;base64,AQ==" }));
      expect(mocks.stop).toHaveBeenCalledTimes(1);
      await act(async () => resume.resolve({}));
      expect(mocks.ask).not.toHaveBeenCalled();
      expect(chatQueue("assistant")).toHaveLength(1);
      await act(async () => stop.resolve({}));
      expect(mocks.ask).toHaveBeenCalledTimes(1);
      expect(mocks.ask.mock.calls[0][2].aborted).toBe(false);
    } finally {
      await act(async () => root.unmount());
    }
  });

  it("shares a single createThread() call and routes concurrent new-chat submissions to the same thread", async () => {
    const createDeferred = deferred<{ id: string }>();
    const firstAskDeferred = deferred<{ error?: string }>();
    const secondAskDeferred = deferred<{ error?: string }>();

    mocks.createThread.mockReturnValue(createDeferred.promise);
    mocks.ask
      .mockReturnValueOnce(firstAskDeferred.promise)
      .mockReturnValueOnce(secondAskDeferred.promise);

    const onThreadReady = vi.fn();
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    try {
      await act(async () => {
        root.render(
          <AssistantPage
            threadId={null}
            onThreadReady={onThreadReady}
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
      });

      const sendButton = () => container.querySelector<HTMLButtonElement>('[data-testid="send-button"]')!;

      // Submit first question on the new-chat composer; createThread() stays pending.
      await act(async () => {
        setComposerDraftText("assistant:__new__", "First question");
      });
      await act(async () => {
        sendButton().click();
        await flushMicrotasks();
      });

      // Submit a second question from the same new-chat session while createThread() is still pending.
      await act(async () => {
        setComposerDraftText("assistant:__new__", "Second question");
      });
      await act(async () => {
        sendButton().click();
        await flushMicrotasks();
      });

      expect(mocks.createThread).toHaveBeenCalledTimes(1);

      // Resolve thread creation; parent switches to the newly created thread.
      await act(async () => {
        createDeferred.resolve({ id: "thread-shared-1" });
        await flushMicrotasks();
      });
      expect(onThreadReady).toHaveBeenCalledTimes(1);
      expect(onThreadReady).toHaveBeenCalledWith("thread-shared-1");

      await act(async () => {
        root.render(
          <AssistantPage
            threadId="thread-shared-1"
            onThreadReady={onThreadReady}
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
        await flushMicrotasks();
      });

      // First question is in flight on thread-shared-1; second question waits in queue.
      expect(mocks.ask).toHaveBeenCalledTimes(1);
      expect(mocks.ask).toHaveBeenNthCalledWith(
        1,
        "First question",
        expect.anything(),
        expect.anything(),
        [],
        "thread-shared-1",
      );

      // Once the first answer finishes, the second question runs on the exact same thread.
      await act(async () => {
        firstAskDeferred.resolve({});
        await flushMicrotasks();
      });
      await act(async () => {
        await flushMicrotasks();
      });

      expect(mocks.ask).toHaveBeenCalledTimes(2);
      expect(mocks.ask).toHaveBeenNthCalledWith(
        2,
        "Second question",
        expect.anything(),
        expect.anything(),
        [],
        "thread-shared-1",
      );
      expect(mocks.createThread).toHaveBeenCalledTimes(1);

      await act(async () => {
        secondAskDeferred.resolve({});
        await flushMicrotasks();
      });
    } finally {
      await act(async () => {
        root.unmount();
      });
      container.remove();
    }
  });

  it("restores image-only attachments and visibly reports errors when a background send fails", async () => {
    const prepareDeferred = deferred<{ ok: true; name: string; dataUrl: string }>();
    const backgroundAskDeferred = deferred<{ error?: string }>();

    mocks.prepareComposerImage.mockReturnValue(prepareDeferred.promise);
    mocks.ask.mockReturnValue(backgroundAskDeferred.promise);

    const photo = new File(["img-bytes"], "jacket-tag.png", { type: "image/png" });
    const attachment = { id: "img-bg-1", file: photo };

    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    try {
      setComposerDraftImages("assistant:thread-bg-a", [attachment]);

      await act(async () => {
        root.render(
          <AssistantPage
            threadId="thread-bg-a"
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
      });

      // Start an image-only send on thread-bg-a while image preparation is pending.
      const sendButton = () => container.querySelector<HTMLButtonElement>('[data-testid="send-button"]')!;
      await act(async () => {
        sendButton().click();
        await flushMicrotasks();
      });

      // Switch to thread-bg-b before preparation finishes.
      await act(async () => {
        root.render(
          <AssistantPage
            threadId="thread-bg-b"
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
        await flushMicrotasks();
      });

      // Resolve preparation so the send dispatches in the background for thread-bg-a.
      await act(async () => {
        prepareDeferred.resolve({ ok: true, name: "jacket-tag.png", dataUrl: "data:image/png;base64,AA==" });
        await flushMicrotasks();
      });

      expect(getComposerDraft("assistant:thread-bg-a").images).toEqual([]);
      expect(mocks.ask).toHaveBeenCalledWith(
        "",
        expect.anything(),
        expect.anything(),
        [{ name: "jacket-tag.png", data_url: "data:image/png;base64,AA==" }],
        "thread-bg-a",
      );

      // Fail the background send while thread-bg-b is still selected.
      await act(async () => {
        backgroundAskDeferred.resolve({ error: "Model quota exceeded." });
        await flushMicrotasks();
      });

      // Attachment is restored to thread-bg-a, and a toast reports the error immediately.
      expect(getComposerDraft("assistant:thread-bg-a").images).toEqual([attachment]);
      expect(mocks.addToast).toHaveBeenCalledWith({ type: "error", title: "Model quota exceeded." });

      // Returning to thread-bg-a keeps the error visible even with 0 saved turns.
      await act(async () => {
        root.render(
          <AssistantPage
            threadId="thread-bg-a"
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
        await flushMicrotasks();
      });

      expect(container.textContent).toContain("Model quota exceeded.");
    } finally {
      await act(async () => {
        root.unmount();
      });
      container.remove();
    }
  });

  it("preserves a failed background submission without overwriting a newer draft in the target thread", async () => {
    const prepareDeferred = deferred<{ ok: true; name: string; dataUrl: string }>();
    const backgroundAskDeferred = deferred<{ error?: string }>();
    const retryAskDeferred = deferred<{ error?: string }>();

    mocks.prepareComposerImage.mockReturnValue(prepareDeferred.promise);
    mocks.ask
      .mockReturnValueOnce(backgroundAskDeferred.promise)
      .mockReturnValueOnce(retryAskDeferred.promise);

    const photo = new File(["img-bytes"], "sleeve-flaw.png", { type: "image/png" });
    const attachment = { id: "img-newer-1", file: photo };

    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    try {
      setComposerDraftText("assistant:thread-newer-a", "Check sleeve flaw pricing");
      setComposerDraftImages("assistant:thread-newer-a", [attachment]);

      await act(async () => {
        root.render(
          <AssistantPage
            threadId="thread-newer-a"
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
      });

      const sendButton = () => container.querySelector<HTMLButtonElement>('[data-testid="send-button"]')!;
      await act(async () => {
        sendButton().click();
        await flushMicrotasks();
      });

      // Switch to thread-newer-b before preparation finishes, then resolve preparation.
      await act(async () => {
        root.render(
          <AssistantPage
            threadId="thread-newer-b"
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
        await flushMicrotasks();
      });

      await act(async () => {
        prepareDeferred.resolve({ ok: true, name: "sleeve-flaw.png", dataUrl: "data:image/png;base64,BB==" });
        await flushMicrotasks();
      });

      // Seller types a brand-new draft into thread-newer-a before the background send fails.
      await act(async () => {
        setComposerDraftText("assistant:thread-newer-a", "Newer unsent follow-up");
      });

      await act(async () => {
        backgroundAskDeferred.resolve({ error: "Upstream timeout." });
        await flushMicrotasks();
      });

      // Newer draft is untouched; toast reports the background failure immediately.
      expect(getComposerDraft("assistant:thread-newer-a").text).toBe("Newer unsent follow-up");
      expect(mocks.addToast).toHaveBeenCalledWith({ type: "error", title: "Upstream timeout." });

      // Returning to thread-newer-a shows the preserved failed text and attachment with Retry.
      await act(async () => {
        root.render(
          <AssistantPage
            threadId="thread-newer-a"
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
        await flushMicrotasks();
      });

      expect(container.textContent).toContain("Upstream timeout.");
      expect(container.textContent).toContain("Check sleeve flaw pricing");
      expect(container.textContent).toContain("sleeve-flaw.png");
      expect(getComposerDraft("assistant:thread-newer-a").text).toBe("Newer unsent follow-up");

      // Retrying resends the complete original submission without touching the newer draft.
      const retryButton = Array.from(container.querySelectorAll("button")).find((btn) => btn.textContent === "Retry");
      expect(retryButton).toBeTruthy();
      await act(async () => {
        retryButton!.click();
        await flushMicrotasks();
      });

      expect(mocks.ask).toHaveBeenNthCalledWith(
        2,
        "Check sleeve flaw pricing",
        expect.anything(),
        expect.anything(),
        [{ name: "sleeve-flaw.png", data_url: "data:image/png;base64,BB==" }],
        "thread-newer-a",
      );
      expect(getComposerDraft("assistant:thread-newer-a").text).toBe("Newer unsent follow-up");

      await act(async () => {
        retryAskDeferred.resolve({});
        await flushMicrotasks();
      });
    } finally {
      await act(async () => {
        root.unmount();
      });
      container.remove();
    }
  });

  it("preserves an image-only new-chat submission when createThread() fails after navigating away", async () => {
    const createDeferred = deferred<{ id: string }>();
    const retryCreateDeferred = deferred<{ id: string }>();
    const retryAskDeferred = deferred<{ error?: string }>();

    mocks.prepareComposerImage.mockResolvedValue({
      ok: true,
      name: "receipt-photo.png",
      dataUrl: "data:image/png;base64,CC==",
    });
    mocks.createThread
      .mockReturnValueOnce(createDeferred.promise)
      .mockReturnValueOnce(retryCreateDeferred.promise);
    mocks.ask.mockReturnValueOnce(retryAskDeferred.promise);

    const photo = new File(["img-bytes"], "receipt-photo.png", { type: "image/png" });
    const attachment = { id: "img-create-fail-1", file: photo };

    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    try {
      setComposerDraftImages("assistant:__new__", [attachment]);

      await act(async () => {
        root.render(
          <AssistantPage
            threadId={null}
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
      });

      const sendButton = () => container.querySelector<HTMLButtonElement>('[data-testid="send-button"]')!;
      await act(async () => {
        sendButton().click();
        await flushMicrotasks();
      });
      expect(getComposerDraft("assistant:__new__").images).toEqual([]);

      // Navigate away to an existing thread while createThread() is still pending.
      await act(async () => {
        root.render(
          <AssistantPage
            threadId="thread-other-nav"
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
        await flushMicrotasks();
      });

      // Fail thread creation after leaving the new-chat session.
      await act(async () => {
        createDeferred.reject(new Error("Database offline"));
        await flushMicrotasks();
      });

      expect(mocks.addToast).toHaveBeenCalledWith({ type: "error", title: "Database offline" });
      expect(getComposerDraft("assistant:__new__").images).toEqual([]);

      // Returning to New chat shows the preserved image-only submission with Retry.
      await act(async () => {
        root.render(
          <AssistantPage
            threadId={null}
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
        await flushMicrotasks();
      });

      expect(container.textContent).toContain("Database offline");
      expect(container.textContent).toContain("receipt-photo.png");

      const retryButton = Array.from(container.querySelectorAll("button")).find((btn) => btn.textContent === "Retry");
      expect(retryButton).toBeTruthy();
      await act(async () => {
        retryButton!.click();
        retryCreateDeferred.resolve({ id: "thread-recovered-new" });
        await flushMicrotasks();
      });

      expect(mocks.ask).toHaveBeenCalledWith(
        "",
        expect.anything(),
        expect.anything(),
        [{ name: "receipt-photo.png", data_url: "data:image/png;base64,CC==" }],
        "thread-recovered-new",
      );

      await act(async () => {
        retryAskDeferred.resolve({});
        await flushMicrotasks();
      });
    } finally {
      await act(async () => {
        root.unmount();
      });
      container.remove();
    }
  });

  it("preserves a failed new-chat submission out-of-band when AssistantPage unmounts and remounts before createThread() rejects", async () => {
    const createDeferred = deferred<{ id: string }>();
    mocks.createThread.mockReturnValueOnce(createDeferred.promise);

    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    try {
      setComposerDraftText("assistant:__new__", "Original unmounted question");

      await act(async () => {
        root.render(
          <AssistantPage
            threadId={null}
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
      });

      const sendButton = () => container.querySelector<HTMLButtonElement>('[data-testid="send-button"]')!;
      await act(async () => {
        sendButton().click();
        await flushMicrotasks();
      });

      // Simulate leaving Assistant (App unmounts AssistantPage) while createThread() is still pending.
      await act(async () => {
        root.unmount();
      });

      // Return to Assistant as a fresh mount and start composing a brand-new draft before the earlier createThread() rejects.
      const remountedRoot = createRoot(container);
      try {
        setComposerDraftText("assistant:__new__", "Brand new draft after returning");
        await act(async () => {
          remountedRoot.render(
            <AssistantPage
              threadId={null}
              onOpenProviders={vi.fn()}
              onOpenListing={vi.fn()}
            />,
          );
        });

        await act(async () => {
          createDeferred.reject(new Error("Thread service unavailable"));
          await flushMicrotasks();
        });

        // Toast fires for the unmounted failure, the fresh draft is untouched, and the remounted page immediately shows the preserved alert.
        expect(mocks.addToast).toHaveBeenCalledWith({ type: "error", title: "Thread service unavailable" });
        expect(getComposerDraft("assistant:__new__").text).toBe("Brand new draft after returning");
        expect(container.textContent).toContain("Thread service unavailable");
        expect(container.textContent).toContain("Original unmounted question");

        const dismissButton = Array.from(container.querySelectorAll("button")).find((btn) => btn.textContent === "Dismiss");
        expect(dismissButton).toBeTruthy();
        await act(async () => {
          dismissButton!.click();
          await flushMicrotasks();
        });
        expect(container.textContent).not.toContain("Original unmounted question");
      } finally {
        await act(async () => {
          remountedRoot.unmount();
        });
      }
    } finally {
      container.remove();
    }
  });

  it("retries a preserved new-chat submission in the background without moving or hijacking a fresh new-chat draft", async () => {
    const failedCreateDeferred = deferred<{ id: string }>();
    const retryCreateDeferred = deferred<{ id: string }>();
    const retryAskDeferred = deferred<{ error?: string }>();
    const freshCreateDeferred = deferred<{ id: string }>();
    const freshAskDeferred = deferred<{ error?: string }>();

    mocks.prepareComposerImage
      .mockResolvedValueOnce({
        ok: true,
        name: "failed-receipt.png",
        dataUrl: "data:image/png;base64,DD==",
      })
      .mockResolvedValueOnce({
        ok: true,
        name: "fresh-tag.png",
        dataUrl: "data:image/png;base64,EE==",
      });
    mocks.createThread
      .mockReturnValueOnce(failedCreateDeferred.promise)
      .mockReturnValueOnce(retryCreateDeferred.promise)
      .mockReturnValueOnce(freshCreateDeferred.promise);
    mocks.ask
      .mockReturnValueOnce(retryAskDeferred.promise)
      .mockReturnValueOnce(freshAskDeferred.promise);

    const failedAttachment = {
      id: "img-retry-failed-1",
      file: new File(["img-bytes"], "failed-receipt.png", { type: "image/png" }),
    };
    const freshAttachment = {
      id: "img-fresh-draft-1",
      file: new File(["img-bytes"], "fresh-tag.png", { type: "image/png" }),
    };

    const onThreadReady = vi.fn();
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    try {
      setComposerDraftText("assistant:__new__", "Failed question to recover");
      setComposerDraftImages("assistant:__new__", [failedAttachment]);

      await act(async () => {
        root.render(
          <AssistantPage
            threadId={null}
            onThreadReady={onThreadReady}
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
      });

      const sendButton = () => container.querySelector<HTMLButtonElement>('[data-testid="send-button"]')!;
      await act(async () => {
        sendButton().click();
        await flushMicrotasks();
      });

      // Navigate away, fail thread creation, then return to New chat.
      await act(async () => {
        root.render(
          <AssistantPage
            threadId="thread-other-before-retry"
            onThreadReady={onThreadReady}
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
        await flushMicrotasks();
      });

      await act(async () => {
        failedCreateDeferred.reject(new Error("Temporary outage"));
        await flushMicrotasks();
      });

      await act(async () => {
        root.render(
          <AssistantPage
            threadId={null}
            onThreadReady={onThreadReady}
            onOpenProviders={vi.fn()}
            onOpenListing={vi.fn()}
          />,
        );
        await flushMicrotasks();
      });

      // Seller composes a fresh text + image draft in assistant:__new__ before clicking Retry.
      await act(async () => {
        setComposerDraftText("assistant:__new__", "Fresh draft in progress");
        setComposerDraftImages("assistant:__new__", [freshAttachment]);
      });

      const retryButton = Array.from(container.querySelectorAll("button")).find((btn) => btn.textContent === "Retry");
      expect(retryButton).toBeTruthy();

      await act(async () => {
        retryButton!.click();
        retryCreateDeferred.resolve({ id: "thread-retried-bg" });
        await flushMicrotasks();
      });

      // Retried submission runs in the background on its own thread; fresh draft and focus stay on New chat.
      expect(onThreadReady).not.toHaveBeenCalled();
      expect(getComposerDraft("assistant:__new__").text).toBe("Fresh draft in progress");
      expect(getComposerDraft("assistant:__new__").images).toEqual([freshAttachment]);
      expect(getComposerDraft("assistant:thread-retried-bg").text).toBe("");
      expect(getComposerDraft("assistant:thread-retried-bg").images).toEqual([]);
      expect(mocks.ask).toHaveBeenNthCalledWith(
        1,
        "Failed question to recover",
        expect.anything(),
        expect.anything(),
        [{ name: "failed-receipt.png", data_url: "data:image/png;base64,DD==" }],
        "thread-retried-bg",
      );

      // Submitting the fresh draft afterwards starts its own distinct thread.
      await act(async () => {
        sendButton().click();
        await flushMicrotasks();
      });
      await act(async () => {
        freshCreateDeferred.resolve({ id: "thread-fresh-own" });
        await flushMicrotasks();
      });

      expect(onThreadReady).toHaveBeenCalledTimes(1);
      expect(onThreadReady).toHaveBeenCalledWith("thread-fresh-own");
      expect(mocks.ask).toHaveBeenNthCalledWith(
        2,
        "Fresh draft in progress",
        expect.anything(),
        expect.anything(),
        [{ name: "fresh-tag.png", data_url: "data:image/png;base64,EE==" }],
        "thread-fresh-own",
      );

      await act(async () => {
        retryAskDeferred.resolve({});
        freshAskDeferred.resolve({});
        await flushMicrotasks();
      });
    } finally {
      await act(async () => {
        root.unmount();
      });
      container.remove();
    }
  });
});
