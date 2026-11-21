// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useChatQueue } from "./useChatQueue";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mounted: Root[] = [];
afterEach(() => act(() => mounted.splice(0).forEach((root) => root.unmount())));

function mountQueue(sendMessage: (text: string) => Promise<boolean>) {
  const root = createRoot(document.createElement("div"));
  mounted.push(root);
  const consumed = vi.fn();
  const pin = () => {};
  const client = new QueryClient();
  function Harness({ busy, serverBusy, queued }: { busy: boolean; serverBusy: boolean; queued: string | null }) {
    useChatQueue({
      convId: "queue-listing",
      busy,
      serverBusy,
      sendMessage,
      queuedMessage: queued,
      onQueuedMessageConsumed: consumed,
      pinChatToBottom: pin,
    });
    return null;
  }
  // The activity query is what ChatPanel derives serverBusy from.
  return (props: { busy: boolean; serverBusy: boolean; queued: string | null }) => act(() => {
    client.setQueryData(["activity", "queue-listing"], { busy: props.serverBusy });
    root.render(<QueryClientProvider client={client}><Harness {...props} /></QueryClientProvider>);
  });
}

describe("held chat follow-ups", () => {
  it("send once the field fill a chat save started has finished", async () => {
    // The Ask-chat prompt arrives while a reply streams; the reply's save then
    // starts a background field fill that outlives it.
    const sendMessage = vi.fn(async () => true);
    const render = mountQueue(sendMessage);
    render({ busy: true, serverBusy: false, queued: "Fill Depop / Style" });
    render({ busy: false, serverBusy: true, queued: null });
    expect(sendMessage).not.toHaveBeenCalled();

    render({ busy: false, serverBusy: false, queued: null });
    await act(async () => {});
    expect(sendMessage).toHaveBeenCalledWith("Fill Depop / Style");
  });
});
