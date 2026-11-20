// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { useComposerImages } from "./ComposerAttachments";
import { clearComposerDraft, getComposerDraft, removeComposerDraftImages, resetComposerDrafts, setComposerDraftImages, setComposerDraftText, transferComposerDraftRemainder, useComposerDraftText } from "./composerDrafts";

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

  it("clears only sent attachment IDs so files added during async prepare are preserved", async () => {
    let latestImages: ReturnType<typeof useComposerImages> | null = null;
    function Harness({ draftKey }: { draftKey: string }) {
      latestImages = useComposerImages(draftKey);
      return <output>{latestImages.images.map((item) => item.file.name).join(",")}</output>;
    }
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    try {
      await act(() => root.render(<Harness draftKey="listing:race" />));
      await act(() => {
        latestImages!.addFiles([makeImageFile("first.png")]);
      });
      const capturedIds = latestImages!.images.map((item) => item.id);
      const staleClear = latestImages!.clear;

      // User adds a second image while the first send is still preparing.
      await act(() => {
        latestImages!.addFiles([makeImageFile("second.png")]);
      });
      expect(latestImages!.images.map((item) => item.file.name)).toEqual(["first.png", "second.png"]);

      // Resolving the first send removes only first.png, preserving second.png.
      await act(() => {
        staleClear(capturedIds);
      });
      expect(latestImages!.images.map((item) => item.file.name)).toEqual(["second.png"]);
    } finally {
      await act(() => root.unmount());
      container.remove();
    }
  });

  it("transfers unsent images and newer text off assistant:__new__ to a newly created thread", () => {
    const imgB = { id: "img-b", file: makeImageFile("second.png"), previewUrl: "blob:second" };

    // Case 1: submitted text matches the draft — drop submitted text, move remaining image to new thread.
    setComposerDraftText("assistant:__new__", "First question");
    setComposerDraftImages("assistant:__new__", [imgB]);
    transferComposerDraftRemainder("assistant:__new__", "assistant:thread-new", "First question");

    expect(getComposerDraft("assistant:__new__")).toEqual({ text: "", images: [] });
    expect(getComposerDraft("assistant:thread-new").text).toBe("");
    expect(getComposerDraft("assistant:thread-new").images).toEqual([imgB]);

    // Case 2: user edited newer text while createThread was in flight — preserve both newer text and image.
    setComposerDraftText("assistant:__new__", "Follow-up thought");
    setComposerDraftImages("assistant:__new__", [imgB]);
    transferComposerDraftRemainder("assistant:__new__", "assistant:thread-2", "First question");

    expect(getComposerDraft("assistant:__new__")).toEqual({ text: "", images: [] });
    expect(getComposerDraft("assistant:thread-2").text).toBe("Follow-up thought");
    expect(getComposerDraft("assistant:thread-2").images).toEqual([imgB]);

    // Case 3: if imgB was already preparing for a queued send when transferred, removing its ID
    // from the destination thread clears imgB while preserving any newer imgC attached later.
    const imgC = { id: "img-c", file: makeImageFile("third.png"), previewUrl: "blob:third" };
    setComposerDraftImages("assistant:thread-2", [imgB, imgC]);
    removeComposerDraftImages("assistant:thread-2", [imgB.id]);
    expect(getComposerDraft("assistant:thread-2").images).toEqual([imgC]);
  });
});
