// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { useComposerImages } from "./ComposerAttachments";
import { clearComposerDraft, resetComposerDrafts, useComposerDraftText } from "./composerDrafts";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function makeImageFile(name: string): File {
  return new File(["img"], name, { type: "image/png" });
}

function makeTextFile(name: string): File {
  return new File(["txt"], name, { type: "text/plain" });
}

describe("composer draft persistence", () => {
  afterEach(() => {
    resetComposerDrafts();
  });

  it("preserves unsent text and pending images across item/thread switches and clears only on send", async () => {
    let latestText = "";
    let latestSetText: ((next: string) => void) | null = null;
    let latestImages: ReturnType<typeof useComposerImages> | null = null;

    function Harness({ draftKey }: { draftKey: string }) {
      const [text, setText] = useComposerDraftText(draftKey);
      const images = useComposerImages(draftKey);
      latestText = text;
      latestSetText = setText;
      latestImages = images;
      return <output>{text}|{images.images.map((item) => item.file.name).join(",")}|{images.error}</output>;
    }

    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    try {
      await act(() => root.render(<Harness draftKey="listing:a" />));
      expect(latestText).toBe("");

      // Type a draft and attach an image + rejected file error on item A.
      await act(() => {
        latestSetText!("Shorten the title");
        latestImages!.addFiles([makeImageFile("tag.png"), makeTextFile("notes.txt")]);
      });
      expect(latestText).toBe("Shorten the title");
      expect(latestImages!.images.map((item) => item.file.name)).toEqual(["tag.png"]);
      expect(latestImages!.error).toContain("notes.txt");

      // Switch to item B: composer starts empty and does not inherit item A's error.
      await act(() => root.render(<Harness draftKey="listing:b" />));
      expect(latestText).toBe("");
      expect(latestImages!.images).toEqual([]);
      expect(latestImages!.error).toBe("");

      // Type a different draft on item B, keeping a stale setter reference for B.
      await act(() => {
        latestSetText!("Drop price to $24");
      });
      const clearItemB = latestSetText!;

      // Switch back to item A: half-written text and pending image return intact.
      await act(() => root.render(<Harness draftKey="listing:a" />));
      expect(latestText).toBe("Shorten the title");
      expect(latestImages!.images.map((item) => item.file.name)).toEqual(["tag.png"]);

      // Clearing item B via its bound setter clears B without clobbering active item A.
      await act(() => {
        clearItemB("");
      });
      expect(latestText).toBe("Shorten the title");

      // Sending on item A clears text and images.
      await act(() => {
        latestImages!.clear();
        latestSetText!("");
      });
      expect(latestText).toBe("");
      expect(latestImages!.images).toEqual([]);

      // Unmount and remount preserves a newly typed draft until explicitly cleared.
      await act(() => root.render(<Harness draftKey="assistant:thread-1" />));
      await act(() => {
        latestSetText!("What sold last week?");
      });
      await act(() => root.unmount());

      const nextRoot = createRoot(container);
      try {
        await act(() => nextRoot.render(<Harness draftKey="assistant:thread-1" />));
        expect(latestText).toBe("What sold last week?");
        await act(() => {
          clearComposerDraft("assistant:thread-1");
        });
        expect(latestText).toBe("");
      } finally {
        await act(() => nextRoot.unmount());
      }
    } finally {
      container.remove();
    }
  });
});
