// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { desktopRuntime, WorkspaceRuntimeProvider } from "../app/runtime";
import { api } from "../api/client";
import { ToastProvider } from "./ui/toast";
import { PhotoTray } from "./PhotoTray";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it("hides a removed photo immediately and restores it when Undo is clicked without deleting", async () => {
  const photo = { id: "photo1", conversation_id: "fixture", original_filename: "fixture.jpg", mime_type: "image/jpeg", size_bytes: 10, display_order: 0, width: 80, height: 100, url: "/api/photos/photo1", created_at: "2026-11-16T00:00:00" };
  const photos = [photo];
  const read = vi.spyOn(api.conversations, "photos").mockImplementation(async () => photos);
  const remove = vi.spyOn(api.conversations, "deletePhoto").mockResolvedValue({ ok: true });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  client.setQueryData(["photos", "fixture"], photos);
  client.setQueryData(["conversation", "fixture"], { id: "fixture", status: "draft" });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(
      <WorkspaceRuntimeProvider value={desktopRuntime}>
        <QueryClientProvider client={client}>
          <ToastProvider>
            <PhotoTray convId="fixture" />
          </ToastProvider>
        </QueryClientProvider>
      </WorkspaceRuntimeProvider>,
    ));
    await act(async () => {
      host.querySelector<HTMLButtonElement>('[aria-label="Delete photo 1"]')!.click();
    });
    expect(host.querySelector('[aria-label="Delete photo 1"]')).toBeNull();
    expect(remove).not.toHaveBeenCalled();

    const undoButton = [...document.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent?.trim() === "Undo",
    );
    expect(undoButton).toBeDefined();
    await act(async () => {
      undoButton!.click();
    });
    expect(host.querySelector('[aria-label="Delete photo 1"]')).not.toBeNull();
    expect(remove).not.toHaveBeenCalled();
  } finally {
    await act(async () => root.unmount());
    client.clear(); host.remove(); read.mockRestore(); remove.mockRestore();
  }
});

it("a marketplace action right after a removal waits for the photo to be deleted first", async () => {
  const photo = { id: "photo1", conversation_id: "fixture", original_filename: "fixture.jpg", mime_type: "image/jpeg", size_bytes: 10, display_order: 0, width: 80, height: 100, url: "/api/photos/photo1", created_at: "2026-11-16T00:00:00" };
  const photos = [photo];
  const order: string[] = [];
  const read = vi.spyOn(api.conversations, "photos").mockImplementation(async () => photos);
  const remove = vi.spyOn(api.conversations, "deletePhoto").mockImplementation(async () => {
    await new Promise((resolve) => setTimeout(resolve, 5));
    order.push("delete");
    return { ok: true };
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    order.push(`${init?.method ?? "GET"} ${String(input)}`);
    return new Response(JSON.stringify({ marketplace: "ebay", state: "listed" }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  client.setQueryData(["photos", "fixture"], photos);
  client.setQueryData(["conversation", "fixture"], { id: "fixture", status: "draft" });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(
      <WorkspaceRuntimeProvider value={desktopRuntime}>
        <QueryClientProvider client={client}>
          <ToastProvider>
            <PhotoTray convId="fixture" />
          </ToastProvider>
        </QueryClientProvider>
      </WorkspaceRuntimeProvider>,
    ));
    await act(async () => {
      host.querySelector<HTMLButtonElement>('[aria-label="Delete photo 1"]')!.click();
    });
    expect(remove).not.toHaveBeenCalled();

    // A plain field save keeps Undo available.
    await act(async () => { await api.conversations.update("fixture", { title: "Coat" }); });
    expect(remove).not.toHaveBeenCalled();

    await act(async () => { await api.nativeMarketplaces.list("fixture", "ebay", "rev1"); });
    expect(remove).toHaveBeenCalledWith("fixture", "photo1");
    expect(order.slice(-2)).toEqual(["delete", "POST /api/conversations/fixture/native-marketplaces/ebay/list"]);
  } finally {
    await act(async () => root.unmount());
    vi.unstubAllGlobals();
    client.clear(); host.remove(); read.mockRestore(); remove.mockRestore();
  }
});

it("shows a refused photo removal and clears that error on a successful retry", async () => {
  const photo = { id: "photo1", conversation_id: "fixture", original_filename: "fixture.jpg", mime_type: "image/jpeg", size_bytes: 10, display_order: 0, width: 80, height: 100, url: "/api/photos/photo1", created_at: "2026-11-16T00:00:00" };
  let photos = [photo];
  const read = vi.spyOn(api.conversations, "photos").mockImplementation(async () => photos);
  const remove = vi.spyOn(api.conversations, "deletePhoto")
    .mockRejectedValueOnce(new Error("Photo removal is temporarily unavailable. Try again."))
    .mockImplementationOnce(async () => { photos = []; return { ok: true }; });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  client.setQueryData(["photos", "fixture"], photos);
  client.setQueryData(["conversation", "fixture"], { id: "fixture", status: "draft" });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(
      <WorkspaceRuntimeProvider value={desktopRuntime}>
        <QueryClientProvider client={client}>
          <ToastProvider>
            <PhotoTray convId="fixture" />
          </ToastProvider>
        </QueryClientProvider>
      </WorkspaceRuntimeProvider>,
    ));
    const clickRemoveAndCommit = async () => {
      await act(async () => {
        host.querySelector<HTMLButtonElement>('[aria-label="Delete photo 1"]')!.click();
      });
      await act(async () => {
        document.querySelector<HTMLButtonElement>('[aria-label="Dismiss notification"]')!.click();
      });
    };
    await clickRemoveAndCommit();
    expect(remove).toHaveBeenCalledWith("fixture", "photo1");
    await vi.waitFor(async () => {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
      expect(host.textContent).toContain("Photo removal is temporarily unavailable. Try again.");
    });
    expect(host.querySelector('[aria-label="Delete photo 1"]')).not.toBeNull();
    await clickRemoveAndCommit();
    expect(remove).toHaveBeenCalledTimes(2);
    expect(host.textContent).not.toContain("Photo removal is temporarily unavailable. Try again.");
    await vi.waitFor(async () => {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
      expect(host.querySelector('[aria-label="Delete photo 1"]')).toBeNull();
    });
  } finally {
    await act(async () => root.unmount());
    client.clear(); host.remove(); read.mockRestore(); remove.mockRestore();
  }
});

it("shows a refused reorder inside the open viewer and rolls back its order", async () => {
  const photos = ["first", "second"].map(id => ({ id, conversation_id: "fixture", original_filename: `${id}.jpg`, mime_type: "image/jpeg", size_bytes: 10, display_order: id === "first" ? 0 : 1, width: 80, height: 100, url: `/api/photos/${id}`, created_at: "2026-11-16T00:00:00" }));
  const read = vi.spyOn(api.conversations, "photos").mockResolvedValue(photos);
  const reorder = vi.spyOn(api.conversations, "reorderPhotos").mockRejectedValue(new Error("Photo order was not saved. Try again."));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  client.setQueryData(["photos", "fixture"], photos);
  client.setQueryData(["conversation", "fixture"], { id: "fixture", status: "draft" });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(<WorkspaceRuntimeProvider value={desktopRuntime}><QueryClientProvider client={client}><PhotoTray convId="fixture" /></QueryClientProvider></WorkspaceRuntimeProvider>));
    await act(async () => host.querySelector<HTMLElement>('[aria-label="View photo 1 enlarged"]')!.click());
    await act(async () => {
      [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(button => button.textContent?.includes("Later"))!.click();
    });
    await vi.waitFor(async () => {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
      expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Photo order was not saved. Try again.");
    });
    expect(reorder).toHaveBeenCalledWith("fixture", ["second", "first"]);
    expect(client.getQueryData(["photos", "fixture"])).toEqual(photos);
  } finally {
    await act(async () => root.unmount());
    client.clear(); host.remove(); read.mockRestore(); reorder.mockRestore();
  }
});
