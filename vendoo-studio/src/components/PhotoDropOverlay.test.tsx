// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { imageFilesFromTransfer } from "../photoDrop";
import { PhotoDropOverlay } from "./PhotoDropOverlay";

vi.mock("../photoDrop", () => ({
  dragHasFiles: () => true,
  imageFilesFromTransfer: vi.fn(),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it("ignores an older drop and pending files after unmount", async () => {
  const root = createRoot(document.createElement("div"));
  const onFiles = vi.fn();
  const pending: Array<(files: File[]) => void> = [];
  vi.mocked(imageFilesFromTransfer).mockImplementation(() =>
    new Promise<File[]>(resolve => { pending.push(resolve); }));
  const files = [new File(["photo"], "photo.jpg", { type: "image/jpeg" })];
  try {
    await act(async () => {
      root.render(<PhotoDropOverlay title="Photos" hint="Drop photos" busy={false} busyLabel="Reading" onFiles={onFiles} />);
    });
    await act(async () => {
      window.dispatchEvent(new Event("drop", { cancelable: true }));
      window.dispatchEvent(new Event("drop", { cancelable: true }));
      pending[0](files);
    });
    expect(onFiles).not.toHaveBeenCalled();
    await act(async () => { pending[1](files); });
    expect(onFiles).toHaveBeenCalledExactlyOnceWith(files);
    await act(async () => {
      window.dispatchEvent(new Event("drop", { cancelable: true }));
      root.unmount();
      pending[2](files);
    });
    expect(onFiles).toHaveBeenCalledTimes(1);
  } finally {
    vi.mocked(imageFilesFromTransfer).mockReset();
  }
});
