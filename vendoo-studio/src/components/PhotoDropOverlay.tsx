import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { dragHasFiles, imageFilesFromTransfer } from "../photoDrop";
import { toastManager } from "./ui/toast";

interface Props {
  title: string;
  hint: string;
  busy: boolean;
  busyLabel: string;
  onFiles: (files: File[]) => void;
}

export function PhotoDropOverlay({ title, hint, busy, busyLabel, onFiles }: Props) {
  const [dragging, setDragging] = useState(false);
  const onFilesRef = useRef(onFiles);
  useEffect(() => {
    onFilesRef.current = onFiles;
  }, [onFiles]);

  useEffect(() => {
    // dragenter/dragleave fire per element, so count them to know when the
    // drag has really left the window.
    const drops = { depth: 0, sequence: 0 };

    const onDragEnter = (event: DragEvent) => {
      if (!dragHasFiles(event.dataTransfer)) return;
      drops.depth += 1;
      setDragging(true);
    };
    const onDragLeave = (event: DragEvent) => {
      if (!dragHasFiles(event.dataTransfer)) return;
      drops.depth = Math.max(0, drops.depth - 1);
      if (drops.depth === 0) setDragging(false);
    };
    const onDragOver = (event: DragEvent) => {
      if (!dragHasFiles(event.dataTransfer)) return;
      // Without this the drop never lands and the webview navigates to the file.
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      // A marketplace form's photos take their own drop; the item-wide prompt would say otherwise.
      setDragging(!(event.target instanceof Element && event.target.closest("[data-marketplace-media]")));
    };
    const onDropCapture = () => {
      drops.depth = 0;
      setDragging(false);
    };
    const onDrop = (event: DragEvent) => {
      if (!dragHasFiles(event.dataTransfer)) return;
      // A component that handles its own file drop already called this.
      if (event.defaultPrevented) return;
      event.preventDefault();
      // dataTransfer is cleared after the handler returns; read entries now.
      const transfer = event.dataTransfer;
      const seq = ++drops.sequence;
      void imageFilesFromTransfer(transfer).then((files) => {
        if (seq !== drops.sequence) return;
        if (files.length) {
          onFilesRef.current(files);
          return;
        }
        toastManager.add({
          type: "error",
          title: "No photos in that drop",
          description: "Drop image files, or folders that contain JPG, PNG, WEBP, or HEIC photos.",
        });
      });
    };

    window.addEventListener("dragenter", onDragEnter, true);
    window.addEventListener("dragleave", onDragLeave, true);
    window.addEventListener("dragover", onDragOver, true);
    window.addEventListener("drop", onDropCapture, true);
    window.addEventListener("drop", onDrop);
    return () => {
      drops.sequence += 1;
      window.removeEventListener("dragenter", onDragEnter, true);
      window.removeEventListener("dragleave", onDragLeave, true);
      window.removeEventListener("dragover", onDragOver, true);
      window.removeEventListener("drop", onDropCapture, true);
      window.removeEventListener("drop", onDrop);
    };
  }, []);

  if (!dragging && !busy) return null;

  return createPortal(
    <div className={`photo-drop-overlay${busy && !dragging ? " is-busy" : ""}`} aria-hidden="true">
      <div className="photo-drop-card">
        <div className="photo-drop-title">{busy && !dragging ? busyLabel : title}</div>
        <div className="photo-drop-hint">{busy && !dragging ? "Hang tight." : hint}</div>
      </div>
    </div>,
    document.body,
  );
}
