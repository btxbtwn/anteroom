import { toastManager } from "../components/ui/toast";

/**
 * Remaining entry point for AssistantPage, which the chat rebuild owns. Every
 * other caller uses `toastManager.add` from components/ui/toast directly.
 * Delete this file once AssistantPage does too.
 */
export function addToast(toast: { type: "success" | "error" | "warning"; title: string; description?: string }) {
  return toastManager.add(toast);
}
