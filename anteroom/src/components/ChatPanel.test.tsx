// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ComponentProps, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatPanel } from "./ChatPanel";
import type { ChatComposer } from "./chat/ChatComposer";
import { getComposerDraft, resetComposerDrafts, setComposerDraftImages } from "./chat/composerDrafts";

const { uploadPhotos, handleGenerate, sendMessage } = vi.hoisted(() => ({
  uploadPhotos: vi.fn(),
  handleGenerate: vi.fn(),
  sendMessage: vi.fn(),
}));

vi.mock("../api/client", () => ({ api: { conversations: { uploadPhotos } } }));
vi.mock("./chat/useChatServerState", () => ({
  useChatServerState: () => ({
    photos: uploadPhotos.mock.calls.length ? [{ id: "photo-1" }] : [],
    messages: [],
    busy: false,
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
      <button onClick={onSend}>Send</button>
      {images.error && <p role="alert">{images.error}</p>}
    </>
  ),
}));

describe("listing photo uploads from chat", () => {
  const draftKey = "listing:listing-a";
  let root: Root;
  let container: HTMLDivElement;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.resetAllMocks();
    resetComposerDrafts();
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

  async function renderChat(convId = "listing-a") {
    await act(async () => root.render(
      <QueryClientProvider client={queryClient}>
        <ChatPanel key={convId} convId={convId} />
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
      .mockResolvedValueOnce({ count: 1, photos: [{ original_filename: "front.jpg" }], errors: ["Try tag.jpg again."] })
      .mockResolvedValueOnce({ count: 1 });

    await renderChat();
    await send();

    expect(uploadPhotos).toHaveBeenNthCalledWith(1, "listing-a", [front, tag]);
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

    expect(uploadPhotos).toHaveBeenNthCalledWith(2, "listing-a", [replace ? correctedTag : tag]);
    expect(getComposerDraft(draftKey).images).toEqual([]);
    expect(handleGenerate).toHaveBeenCalledTimes(1);
    expect(sendMessage).not.toHaveBeenCalled();
  });
});
