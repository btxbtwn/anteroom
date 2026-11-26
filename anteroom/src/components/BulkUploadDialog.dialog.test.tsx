// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { desktopRuntime, WorkspaceRuntimeProvider } from "../app/runtime";
import { api } from "../api/client";
import type { Conversation, ListingPhoto } from "../api/types";
import { BulkUploadDialog } from "./BulkUploadDialog";
import { RegenerateReviewDialog } from "./RegenerateReviewDialog";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

async function settle(check: () => void) {
  await vi.waitFor(async () => {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    check();
  });
}

function pressEscape(target: EventTarget = document.activeElement ?? document.body) {
  target.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
}

describe("BulkUploadDialog shared dialog contract", () => {
  const groups = [{ folder: "Coat", files: [new File(["img"], "coat.jpg", { type: "image/jpeg" })] }];

  async function mountBulk(onCancel = vi.fn()) {
    vi.spyOn(api.boxes, "list").mockResolvedValue({ boxes: [], stores: [] });
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL() { return "blob:coat"; }
      static revokeObjectURL() {}
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const host = document.createElement("div");
    const outsideButton = document.createElement("button");
    outsideButton.textContent = "Outside page control";
    document.body.append(outsideButton, host);
    const root = createRoot(host);
    await act(async () => root.render(
      <WorkspaceRuntimeProvider value={desktopRuntime}>
        <QueryClientProvider client={client}>
          <BulkUploadDialog groups={groups} onCancel={onCancel} onConfirm={vi.fn()} />
        </QueryClientProvider>
      </WorkspaceRuntimeProvider>,
    ));
    return { root, client, host, outsideButton, onCancel };
  }

  it("uses shared ui/dialog, Button, Input, icon close button, and plain Cost of goods label", async () => {
    const { root, client, host } = await mountBulk();
    try {
      const popup = document.querySelector<HTMLElement>('[data-slot="dialog-popup"]');
      expect(popup).not.toBeNull();
      expect(popup?.getAttribute("role")).toBe("dialog");
      expect(document.querySelector(".confirm-dialog")).toBeNull();
      expect(popup?.querySelector(".btn")).toBeNull();
      expect(popup?.querySelector("input.input")).toBeNull();
      expect(popup?.querySelectorAll('[data-slot="button"]').length).toBeGreaterThanOrEqual(4);
      expect(popup?.querySelectorAll('[data-slot="input"]').length).toBeGreaterThanOrEqual(4);

      const close = popup?.querySelector<HTMLButtonElement>('[aria-label="Cancel bulk upload"]');
      expect(close).not.toBeNull();
      expect(close?.textContent?.trim()).toBe("");
      expect(close?.querySelector("svg")).not.toBeNull();

      expect(popup?.textContent).toContain("Cost of goods");
      expect(popup?.textContent).not.toContain("Cost of goods (COG)");
      expect(popup?.textContent).not.toContain("Box, COG");
    } finally {
      await act(async () => root.unmount());
      client.clear();
      host.remove();
    }
  });

  it("traps focus inside the dialog and closes on Escape (closing photo zoom first)", async () => {
    const onCancel = vi.fn();
    const { root, client, host, outsideButton } = await mountBulk(onCancel);
    try {
      // Base UI Dialog installs focus guards and hides background content from assistive tech.
      expect(document.querySelectorAll("[data-base-ui-focus-guard]").length).toBeGreaterThan(0);
      expect(outsideButton.getAttribute("aria-hidden")).toBe("true");

      // Opening photo zoom traps Escape inside the zoom first; outer dialog stays open.
      const thumb = document.querySelector<HTMLElement>(".photo-thumb")!;
      await act(async () => thumb.click());
      expect(document.querySelector('[aria-label="Photo 1 of 1"]')).not.toBeNull();

      await act(async () => pressEscape(document.activeElement ?? document));
      expect(document.querySelector('[aria-label="Photo 1 of 1"]')).toBeNull();
      expect(onCancel).not.toHaveBeenCalled();

      // Pressing Escape again closes the bulk upload dialog.
      await act(async () => pressEscape(document.querySelector('[data-slot="dialog-popup"]')!));
      expect(onCancel).toHaveBeenCalledTimes(1);
    } finally {
      await act(async () => root.unmount());
      client.clear();
      host.remove();
    }
  });
});

describe("RegenerateReviewDialog shared dialog contract", () => {
  const listings = [{ id: "c-coat", title: "Coat" }];
  const photos: ListingPhoto[] = [{
    id: "p1",
    conversation_id: "c-coat",
    original_filename: "1.jpg",
    mime_type: "image/jpeg",
    size_bytes: 100,
    display_order: 0,
    width: 100,
    height: 100,
    created_at: "2026-11-20T00:00:00Z",
    url: "/api/conversations/c-coat/photos/p1/bytes?v=abc",
  }];

  async function mountRegen({
    onClose = vi.fn(),
    updateMock = async () => ({} as Conversation),
  }: {
    onClose?: () => void;
    updateMock?: () => Promise<Conversation>;
  } = {}) {
    vi.spyOn(api.conversations, "regenerateDetails").mockResolvedValue({ notes: JSON.stringify({ size: "M" }) });
    vi.spyOn(api.conversations, "photos").mockResolvedValue(photos);
    vi.spyOn(api.conversations, "get").mockResolvedValue({
      id: "c-coat",
      title: "Coat",
      status: "draft",
      created_at: "2026-11-20T00:00:00Z",
      updated_at: "2026-11-20T00:00:00Z",
      notes: JSON.stringify({ size: "M" }),
    } as Conversation);
    vi.spyOn(api.conversations, "update").mockImplementation(updateMock);

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const host = document.createElement("div");
    const outsideButton = document.createElement("button");
    outsideButton.textContent = "Outside page control";
    document.body.append(outsideButton, host);
    const root = createRoot(host);
    await act(async () => root.render(
      <WorkspaceRuntimeProvider value={desktopRuntime}>
        <QueryClientProvider client={client}>
          <RegenerateReviewDialog listings={listings} onClose={onClose} onRewrite={vi.fn()} />
        </QueryClientProvider>
      </WorkspaceRuntimeProvider>,
    ));
    await settle(() => expect(document.querySelector('[aria-label="Size for Coat"]')).not.toBeNull());
    return { root, client, host, outsideButton, onClose };
  }

  it("uses shared ui/dialog, Button, Input, icon close button, and plain Cost of goods label", async () => {
    const { root, client, host } = await mountRegen();
    try {
      const popup = document.querySelector<HTMLElement>('[data-slot="dialog-popup"]');
      expect(popup).not.toBeNull();
      expect(popup?.getAttribute("role")).toBe("dialog");
      expect(document.querySelector(".confirm-dialog")).toBeNull();
      expect(popup?.querySelector(".btn")).toBeNull();
      expect(popup?.querySelector("input.input")).toBeNull();
      expect(popup?.querySelectorAll('[data-slot="button"]').length).toBeGreaterThanOrEqual(4);
      expect(popup?.querySelectorAll('[data-slot="input"]').length).toBeGreaterThanOrEqual(4);

      const close = popup?.querySelector<HTMLButtonElement>('[aria-label="Close without rewriting"]');
      expect(close).not.toBeNull();
      expect(close?.textContent?.trim()).toBe("");
      expect(close?.querySelector("svg")).not.toBeNull();

      expect(popup?.textContent).toContain("Cost of goods");
      expect(popup?.textContent).not.toContain("Cost of goods (COG)");
    } finally {
      await act(async () => root.unmount());
      client.clear();
      host.remove();
    }
  });

  it("traps focus inside the dialog and closes on Escape (closing photo zoom first, blocked while saving)", async () => {
    const onClose = vi.fn();
    let resolveUpdate!: (conv: Conversation) => void;
    const updateMock = vi.fn().mockImplementation(() => new Promise<Conversation>((resolve) => { resolveUpdate = resolve; }));
    const { root, client, host, outsideButton } = await mountRegen({ onClose, updateMock });
    try {
      expect(document.querySelectorAll("[data-base-ui-focus-guard]").length).toBeGreaterThan(0);
      expect(outsideButton.getAttribute("aria-hidden")).toBe("true");

      // Open photo zoom; first Escape closes only the zoom.
      await settle(() => expect(document.querySelector(".photo-thumb")).not.toBeNull());
      await act(async () => document.querySelector<HTMLElement>(".photo-thumb")!.click());
      expect(document.querySelector('[aria-label="Photo 1 of 1"]')).not.toBeNull();

      await act(async () => pressEscape(document.activeElement ?? document));
      expect(document.querySelector('[aria-label="Photo 1 of 1"]')).toBeNull();
      expect(onClose).not.toHaveBeenCalled();

      // While saving edits, Escape does not close the dialog.
      const sizeInput = document.querySelector<HTMLInputElement>('[aria-label="Size for Coat"]')!;
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(sizeInput, "L");
        sizeInput.dispatchEvent(new Event("input", { bubbles: true }));
      });
      await act(async () => {
        document.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      });
      await act(async () => pressEscape(document.querySelector('[data-slot="dialog-popup"]')!));
      expect(onClose).not.toHaveBeenCalled();

      // Finish saving and check Escape closes when idle.
      await act(async () => resolveUpdate({} as Conversation));
    } finally {
      await act(async () => root.unmount());
      client.clear();
      host.remove();
    }

    const onCloseIdle = vi.fn();
    const second = await mountRegen({ onClose: onCloseIdle });
    try {
      await act(async () => pressEscape(document.querySelector('[data-slot="dialog-popup"]')!));
      expect(onCloseIdle).toHaveBeenCalledTimes(1);
    } finally {
      await act(async () => second.root.unmount());
      second.client.clear();
      second.host.remove();
    }
  });
});
