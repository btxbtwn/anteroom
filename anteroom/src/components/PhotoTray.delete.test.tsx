// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { desktopRuntime, WorkspaceRuntimeProvider } from "../app/runtime";
import { api } from "../api/client";
import { PhotoTray } from "./PhotoTray";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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
    await act(async () => root.render(<WorkspaceRuntimeProvider value={desktopRuntime}><QueryClientProvider client={client}><PhotoTray convId="fixture" /></QueryClientProvider></WorkspaceRuntimeProvider>));
    const clickRemove = async () => {
      await act(async () => {
        host.querySelector<HTMLButtonElement>('[aria-label="Delete photo 1"]')!.click();
      });
    };
    await clickRemove();
    expect(remove).toHaveBeenCalledWith("fixture", "photo1");
    await vi.waitFor(async () => {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
      expect(host.textContent).toContain("Photo removal is temporarily unavailable. Try again.");
    });
    expect(host.querySelector('[aria-label="Delete photo 1"]')).not.toBeNull();
    await clickRemove();
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
