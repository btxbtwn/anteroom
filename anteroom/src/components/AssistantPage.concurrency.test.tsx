// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
  ChatComposer: ({ onSend }: { onSend: () => void }) => (
    <button type="button" data-testid="send-button" onClick={onSend}>
      Send
    </button>
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
});
