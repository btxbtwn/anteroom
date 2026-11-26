// Ported from T3 Code (https://github.com/pingdotgg/t3code), MIT License,
// (c) 2026 T3 Tools Inc. Adapted only where it imported T3-specific modules.

import type { ToastManagerAddOptions } from "@base-ui/react/toast";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { isToastProviderMounted, toastManager, type ThreadToastData } from "./toast";

/** Undo snackbar window: long enough to notice and click Undo (Material 3 / WCAG / Polaris). Base UI pauses on hover and focus. */
export const UNDO_TOAST_TIMEOUT_MS = 7_000;

export type UndoToastOptions = {
  title: ReactNode;
  description?: ReactNode;
  /** Runs when the seller clicks Undo before the toast closes. */
  onUndo: () => void | Promise<void>;
  /** Optional deferred commit that runs once if the toast expires, is dismissed, or is flushed without Undo. */
  onCommit?: () => void | Promise<void>;
  /** Error toast title if an async onUndo rejects. */
  undoErrorTitle?: string;
  /** Button label; defaults to "Undo". */
  undoLabel?: string;
  timeout?: number;
};

export type UndoToastHandle = {
  /** Commits immediately if not already settled (used on unmount, navigation, or pagehide). */
  flush: () => void;
  /** Dismisses the toast without running onUndo or onCommit. */
  cancel: () => void;
};

/**
 * Shared "done · Undo" toast pattern on Anteroom's existing Base UI toast system.
 * Supports both immediate-removal + restore-on-Undo and deferred-commit removals.
 */
export function showUndoToast(options: UndoToastOptions): UndoToastHandle {
  const {
    title,
    description,
    onUndo,
    onCommit,
    undoErrorTitle = "Could not undo",
    undoLabel = "Undo",
    timeout = UNDO_TOAST_TIMEOUT_MS,
  } = options;

  let settled = false;
  let fallbackTimer: ReturnType<typeof setTimeout> | null = null;
  let toastId: ReturnType<typeof toastManager.add> | null = null;

  const clearFallbackTimer = () => {
    if (fallbackTimer !== null) {
      clearTimeout(fallbackTimer);
      fallbackTimer = null;
    }
  };

  const commit = () => {
    if (settled) return;
    settled = true;
    clearFallbackTimer();
    if (onCommit) {
      void Promise.resolve(onCommit()).catch(() => undefined);
    }
  };

  const handleUndo = () => {
    if (settled) return;
    settled = true;
    clearFallbackTimer();
    if (toastId !== null) {
      toastManager.close(toastId);
    }
    void Promise.resolve(onUndo()).catch((error: unknown) => {
      toastManager.add({
        type: "error",
        title: undoErrorTitle,
        description: error instanceof Error ? error.message : "Try again.",
      });
    });
  };

  toastId = toastManager.add({
    title,
    description,
    timeout,
    onClose: commit,
    actionProps: {
      children: undoLabel,
      onClick: handleUndo,
    },
    data: {
      actionVariant: "outline",
      onClose: commit,
    },
  });

  // When no ToastProvider is mounted (e.g., isolated unit tests), Base UI's timer does not run.
  if (onCommit && !isToastProviderMounted() && timeout > 0) {
    fallbackTimer = setTimeout(commit, timeout);
  }

  return {
    flush: () => {
      if (settled) return;
      commit();
      if (toastId !== null) {
        toastManager.close(toastId);
      }
    },
    cancel: () => {
      if (settled) return;
      settled = true;
      clearFallbackTimer();
      if (toastId !== null) {
        toastManager.close(toastId);
      }
    },
  };
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
