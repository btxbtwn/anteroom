// Ported from T3 Code (https://github.com/pingdotgg/t3code), MIT License,
// (c) 2026 T3 Tools Inc. Adapted only where it imported T3-specific modules.

import type { ToastManagerAddOptions } from "@base-ui/react/toast";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { toastManager, type ThreadToastData } from "./toast";

/** Undo snackbar window: long enough to notice and click Undo (Material 3 / WCAG / Polaris). Base UI pauses on hover and focus. */
export const UNDO_TOAST_TIMEOUT_MS = 7_000;

export type UndoToastOptions = {
  title: ReactNode;
  description?: ReactNode;
  /** Runs once when the seller clicks Undo before the toast closes to restore the removed item on the server. */
  onUndo: () => void | Promise<void>;
  /** Error toast title if restoring fails. */
  undoErrorTitle?: string;
  /** Button label; defaults to "Undo". */
  undoLabel?: string;
  timeout?: number;
};

/**
 * Shared "done · Undo" toast pattern on Anteroom's existing Base UI toast system.
 * The removal has already happened on the server; clicking Undo runs `onUndo` to restore it.
 */
export function showUndoToast(options: UndoToastOptions): void {
  const {
    title,
    description,
    onUndo,
    undoErrorTitle = "Could not undo",
    undoLabel = "Undo",
    timeout = UNDO_TOAST_TIMEOUT_MS,
  } = options;

  let undone = false;
  let toastId: ReturnType<typeof toastManager.add> | null = null;

  const handleUndo = () => {
    if (undone) return;
    undone = true;
    if (toastId !== null) {
      toastManager.close(toastId);
    }
    void (async () => {
      try {
        await onUndo();
      } catch (error: unknown) {
        toastManager.add({
          type: "error",
          title: undoErrorTitle,
          description: error instanceof Error && error.message ? error.message : "Try again.",
        });
      }
    })();
  };

  toastId = toastManager.add({
    title,
    description,
    timeout,
    actionProps: {
      children: undoLabel,
      onClick: handleUndo,
    },
    data: {
      actionVariant: "outline",
    },
  });
}

export type StackedThreadToastOptions = {
  type: "error" | "warning" | "success" | "info" | "loading";
  title: ReactNode;
  description?: ReactNode;
  timeout?: number;
  priority?: "low" | "high";
  actionProps?: ComponentPropsWithoutRef<"button">;
  actionVariant?: ThreadToastData["actionVariant"];
  data?: Omit<ThreadToastData, "actionLayout">;
};

export function stackedThreadToast(
  options: StackedThreadToastOptions,
): ToastManagerAddOptions<ThreadToastData> {
  const { type, title, description, timeout, priority, actionProps, actionVariant, data } = options;

  const mergedData: ThreadToastData = {
    ...(data !== undefined ? data : {}),
    actionLayout: "stacked-end",
  };
  if (actionVariant !== undefined) {
    mergedData.actionVariant = actionVariant;
  }

  const payload: ToastManagerAddOptions<ThreadToastData> = {
    type,
    title,
    data: mergedData,
  };

  if (description !== undefined) {
    payload.description = description;
  }
  if (timeout !== undefined) {
    payload.timeout = timeout;
  }
  if (priority !== undefined) {
    payload.priority = priority;
  }
  if (actionProps !== undefined) {
    payload.actionProps = actionProps;
  }

  return payload;
}
