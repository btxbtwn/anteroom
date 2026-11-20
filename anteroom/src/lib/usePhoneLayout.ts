import { useSyncExternalStore } from "react";

// Tailwind's `sm` breakpoint: below it the screen is a phone.
const PHONE = "(max-width: 639.98px)";

function subscribe(onChange: () => void): () => void {
  const media = typeof window === "undefined" ? undefined : window.matchMedia?.(PHONE);
  media?.addEventListener("change", onChange);
  return () => media?.removeEventListener("change", onChange);
}

/** Whether the window is phone-sized, kept current as it resizes. */
export function usePhoneLayout(): boolean {
  return useSyncExternalStore(subscribe, () => Boolean(window.matchMedia?.(PHONE).matches), () => false);
}
