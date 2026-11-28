// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MessageCopyButton } from "./chat/TimelineRows";
import { MarkdownCodeBlock } from "./MarkdownBlocks";
import { toastManager } from "./ui/toast";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  vi.restoreAllMocks();
});

describe("clipboard copy failure toasts", () => {
  it("shows a 'Couldn't copy' toast in MessageCopyButton when clipboard write fails", async () => {
    const addSpy = vi.spyOn(toastManager, "add");
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("NotAllowedError")) },
    });

    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => {
        root.render(<MessageCopyButton text="Hello seller" />);
      });
      const button = host.querySelector<HTMLButtonElement>('[aria-label="Copy message"]');
      expect(button).not.toBeNull();
      await act(async () => {
        button!.click();
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(addSpy).toHaveBeenCalledWith(expect.objectContaining({ type: "error", title: "Couldn't copy" }));
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });

  it("shows a 'Couldn't copy' toast in MarkdownCodeBlock when clipboard write fails", async () => {
    const addSpy = vi.spyOn(toastManager, "add");
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("NotAllowedError")) },
    });

    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => {
        root.render(
          <MarkdownCodeBlock>
            <code className="language-bash">echo hi</code>
          </MarkdownCodeBlock>,
        );
      });
      const button = host.querySelector<HTMLButtonElement>('[aria-label="Copy code"]');
      expect(button).not.toBeNull();
      await act(async () => {
        button!.click();
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(addSpy).toHaveBeenCalledWith(expect.objectContaining({ type: "error", title: "Couldn't copy" }));
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});
