// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { chatQueue, enqueueChatMessage, newQueuedChatMessage, resetChatQueuesForTest } from "./chatQueueStore";
import { listingQueueKey, useChatQueue } from "./useChatQueue";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mounted: Root[] = [];
beforeEach(() => {
  localStorage.clear();
  resetChatQueuesForTest();
});
afterEach(() => act(() => mounted.splice(0).forEach((root) => root.unmount())));

type HarnessProps = { busy: boolean; serverBusy: boolean; queued: string | null; convId?: string; paused?: boolean };

function mountQueue(
  sendMessage: (text: string) => Promise<boolean>,
  interruptAnswer: () => Promise<boolean> = async () => false,
) {
  const root = createRoot(document.createElement("div"));
  mounted.push(root);
  const consumed = vi.fn();
  const pin = () => {};
  const client = new QueryClient();
  const handle: { steer?: ReturnType<typeof useChatQueue>["steer"]; sendNow?: ReturnType<typeof useChatQueue>["sendNow"] } = {};
  function Harness({ busy, serverBusy, queued, convId = "queue-listing", paused }: HarnessProps) {
    const queue = useChatQueue({
      convId,
      busy,
      serverBusy,
      paused,
      sendMessage,
      interruptAnswer,
      queuedMessage: queued,
      onQueuedMessageConsumed: consumed,
      pinChatToBottom: pin,
    });
    handle.steer = queue.steer;
    handle.sendNow = queue.sendNow;
    return null;
  }
  // The activity query is what ChatPanel derives serverBusy from.
  const render = (props: HarnessProps) => act(() => {
    client.setQueryData(["activity", props.convId ?? "queue-listing"], { busy: props.serverBusy });
    root.render(<QueryClientProvider client={client}><Harness {...props} /></QueryClientProvider>);
  });
  return { render, handle };
}

describe("held chat follow-ups", () => {
  it("send once the field fill a chat save started has finished", async () => {
    // The Ask-chat prompt arrives while a reply streams; the reply's save then
    // starts a background field fill that outlives it.
    const sendMessage = vi.fn(async () => true);
    const { render } = mountQueue(sendMessage);
    render({ busy: true, serverBusy: false, queued: "Fill Depop / Style" });
    render({ busy: false, serverBusy: true, queued: null });
    expect(sendMessage).not.toHaveBeenCalled();

    render({ busy: false, serverBusy: false, queued: null });
    await act(async () => {});
    expect(sendMessage).toHaveBeenCalledWith("Fill Depop / Style", []);
  });

  it("keeps a listing's follow-ups when the seller switches to another listing and back", async () => {
    const sendMessage = vi.fn(async () => true);
    const { render } = mountQueue(sendMessage);
    render({ convId: "a", busy: true, serverBusy: false, queued: "Shorter title" });
    render({ convId: "b", busy: false, serverBusy: false, queued: null });
    await act(async () => {});
    expect(sendMessage).not.toHaveBeenCalled();
    expect(chatQueue(listingQueueKey("a")).map((m) => m.text)).toEqual(["Shorter title"]);

    render({ convId: "a", busy: false, serverBusy: false, queued: null });
    await act(async () => {});
    expect(sendMessage).toHaveBeenCalledWith("Shorter title", []);
  });

  it("keeps follow-ups across a reload", () => {
    enqueueChatMessage(listingQueueKey("a"), newQueuedChatMessage("Lower the price"));
    resetChatQueuesForTest();
    expect(chatQueue(listingQueueKey("a")).map((m) => m.text)).toEqual(["Lower the price"]);
  });

  it("holds the queue while one of its messages is being edited", async () => {
    const sendMessage = vi.fn(async () => true);
    const { render } = mountQueue(sendMessage);
    render({ busy: true, serverBusy: false, queued: "Add measurements" });
    render({ busy: false, serverBusy: false, queued: null, paused: true });
    await act(async () => {});
    expect(sendMessage).not.toHaveBeenCalled();
    render({ busy: false, serverBusy: false, queued: null, paused: false });
    await act(async () => {});
    expect(sendMessage).toHaveBeenCalledWith("Add measurements", []);
  });

  it("a steer cuts off the reply and goes ahead of what was already queued", async () => {
    const sendMessage = vi.fn(async () => true);
    const interrupt = vi.fn(async () => true);
    const { render, handle } = mountQueue(sendMessage, interrupt);
    render({ busy: true, serverBusy: false, queued: "Queued first" });
    await act(async () => {
      await handle.steer?.(newQueuedChatMessage("Actually, make it blue"));
    });
    expect(interrupt).toHaveBeenCalledTimes(1);
    expect(chatQueue(listingQueueKey("queue-listing")).map((m) => m.text)).toEqual(["Actually, make it blue", "Queued first"]);

    render({ busy: false, serverBusy: false, queued: null });
    await act(async () => {});
    expect(sendMessage).toHaveBeenNthCalledWith(1, "Actually, make it blue", []);
  });

  it("send now moves a queued message to the front and cuts off the reply", async () => {
    const sendMessage = vi.fn(async () => true);
    const interrupt = vi.fn(async () => true);
    const { render, handle } = mountQueue(sendMessage, interrupt);
    render({ busy: true, serverBusy: false, queued: "first" });
    render({ busy: true, serverBusy: false, queued: "second" });
    const second = chatQueue(listingQueueKey("queue-listing"))[1];
    await act(async () => {
      await handle.sendNow?.(second.id);
    });
    expect(interrupt).toHaveBeenCalledTimes(1);
    expect(chatQueue(listingQueueKey("queue-listing")).map((m) => [m.text, Boolean(m.steer)])).toEqual([
      ["second", true],
      ["first", false],
    ]);
  });

  it("a listing's queue still sends while another listing is mid-send", async () => {
    let finishA: (started: boolean) => void = () => {};
    const sendMessage = vi.fn((text: string) =>
      text === "for a" ? new Promise<boolean>((resolve) => { finishA = resolve; }) : Promise.resolve(true));
    const { render } = mountQueue(sendMessage);
    enqueueChatMessage(listingQueueKey("b"), newQueuedChatMessage("for b"));
    render({ convId: "a", busy: true, serverBusy: false, queued: "for a" });
    render({ convId: "a", busy: false, serverBusy: false, queued: null });
    await act(async () => {});
    expect(sendMessage).toHaveBeenCalledWith("for a", []);
    render({ convId: "b", busy: false, serverBusy: false, queued: null });
    await act(async () => {});
    expect(sendMessage).toHaveBeenCalledWith("for b", []);
    finishA(true);
  });
});
