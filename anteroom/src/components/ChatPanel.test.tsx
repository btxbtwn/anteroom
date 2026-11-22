// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ComponentProps, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatPanel } from "./ChatPanel";
import type { ChatComposer } from "./chat/ChatComposer";
import { getComposerDraft, resetComposerDrafts, setComposerDraftImages, setComposerDraftText } from "./chat/composerDrafts";

import { getLive, patchLive, resetChatLive } from "./chat/liveChatStore";

const { uploadPhotos, handleGenerate, sendMessage } = vi.hoisted(() => ({
  uploadPhotos: vi.fn(),
  handleGenerate: vi.fn(),
  sendMessage: vi.fn(),
}));

vi.mock("../api/client", () => ({ api: { conversations: { uploadListingPhoto: uploadPhotos } } }));
vi.mock("./chat/useChatServerState", () => ({
  useChatServerState: (_convId: string, busy: boolean) => ({
    photos: uploadPhotos.mock.calls.length ? [{ id: "photo-1" }] : [],
    messages: [],
    busy,
  }),
}));
vi.mock("./chat/useChatStream", () => ({
  useGenerateRecovery: () => {},
  useChatStream: () => ({ handleGenerate, sendMessage }),
}));
vi.mock("./chat/ChatTranscript", () => ({
  ChatTranscript: ({ children }: { children: ReactNode }) => <>{children}</>,
}));
vi.mock("./chat/ChatComposer", () => ({
  ChatComposer: ({ images, onSend }: ComponentProps<typeof ChatComposer>) => (
    <>
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
    resetComposerDrafts();
    resetChatLive("listing-a");
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

});
