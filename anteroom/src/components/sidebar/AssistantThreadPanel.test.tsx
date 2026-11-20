// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { ApiRequestError } from "../../api/transport";
import { AssistantThreadPanel } from "./AssistantThreadPanel";

vi.mock("../../ui/confirmDialog", () => ({ confirmDialog: vi.fn(async () => true) }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it.each([409, 503])("shows refused deletion (%i), keeps the thread, and clears the error after retry", async (status) => {
  const thread = { id: "active-fixture", title: "Fixture answer", created_at: null, updated_at: null };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(["assistant", "threads"], [thread]);
  const message = status === 409 ? "That conversation is still being answered." : "Fixture connection unavailable.";
  const remove = vi.spyOn(api.assistant, "deleteThread")
    .mockRejectedValueOnce(new ApiRequestError(message, status, false))
    .mockResolvedValueOnce({ ok: true });
  const onSelect = vi.fn();
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><AssistantThreadPanel selectedId={thread.id} onSelect={onSelect} onCloseMobile={vi.fn()} /></QueryClientProvider>));
    const clickDelete = async () => {
      await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Delete Fixture answer"]')!.click());
    };
    await clickDelete();
    await vi.waitFor(async () => { await act(async () => { expect(host.textContent).toContain(message); }); });
    if (status === 409) expect(host.textContent).toContain("Stop answering or wait for the answer to finish");
    else expect(host.textContent).not.toContain("Stop answering or wait for the answer to finish");
    expect(client.getQueryData(["assistant", "threads"])).toEqual([thread]);
    expect(onSelect).not.toHaveBeenCalled();
    await clickDelete();
    await vi.waitFor(async () => { await act(async () => {
      expect(onSelect).toHaveBeenCalledWith(null);
      expect(host.textContent).not.toContain(message);
    }); });
    expect(client.getQueryData(["assistant", "threads"])).toEqual([]);
  } finally {
    await act(async () => root.unmount());
    client.clear(); host.remove(); remove.mockRestore();
  }
});
