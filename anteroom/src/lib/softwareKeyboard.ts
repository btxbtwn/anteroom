/**
 * Keeps the app inside the part of a phone screen the on-screen keyboard leaves.
 *
 * iOS Safari does not shrink the layout viewport for the keyboard: it slides
 * the page up instead, which pushed the header off screen and could leave the
 * composer behind the keyboard. Following the visual viewport fixes both. The
 * open/closed test is T3 Code's software keyboard store (MIT, (c) 2026 T3 Tools
 * Inc.), itself adapted from Ionic's keyboard assist (MIT, (c) 2015-present
 * Drifty Co.).
 *
 * While the keyboard is open `<html>` carries `data-keyboard="open"`,
 * `--app-height` (the visible height) and `--keyboard-inset` (what the keyboard
 * covers). Closed, both variables are removed and CSS falls back to 100dvh.
 */

/** A height drop smaller than this is browser chrome (the URL bar), not a keyboard. */
export const KEYBOARD_THRESHOLD = 150;

export type ViewportSnapshot = { width: number; height: number; scale: number };

export type KeyboardState = {
  open: boolean;
  /** The height left above the keyboard, in CSS pixels, when it is open. */
  visibleHeight: number | null;
  /** How much of the layout viewport the keyboard covers, when it is open. */
  inset: number;
};

export const KEYBOARD_CLOSED: KeyboardState = { open: false, visibleHeight: null, inset: 0 };

/** Whether the keyboard is open, from the window and visual viewport sizes. */
export function keyboardState(innerHeight: number, viewport: ViewportSnapshot | null): KeyboardState {
  if (!viewport) return KEYBOARD_CLOSED;
  // Pinch zoom also shrinks the visual viewport; leave the layout alone then.
  if (viewport.scale > 1.01) return KEYBOARD_CLOSED;
  const covered = innerHeight - viewport.height * viewport.scale;
  if (covered <= KEYBOARD_THRESHOLD) return KEYBOARD_CLOSED;
  return { open: true, visibleHeight: Math.round(viewport.height), inset: Math.round(covered) };
}

type KeyboardWindow = Pick<Window, "innerHeight" | "scrollY" | "scrollTo" | "matchMedia"> & {
  visualViewport: (EventTarget & ViewportSnapshot) | null;
  document: { documentElement: HTMLElement };
};

/** Follow the visual viewport on touch devices. Returns a cleanup function. */
export function installSoftwareKeyboard(win: KeyboardWindow): () => void {
  const viewport = win.visualViewport;
  if (!viewport || !win.matchMedia("(pointer: coarse)").matches) return () => {};
  const root = win.document.documentElement;
  let current = KEYBOARD_CLOSED;

  const apply = () => {
    const next = keyboardState(win.innerHeight, viewport);
    if (next.open) {
      root.dataset.keyboard = "open";
      root.style.setProperty("--app-height", `${next.visibleHeight}px`);
      root.style.setProperty("--keyboard-inset", `${next.inset}px`);
      // The app now fits above the keyboard; undo the page slide iOS adds on focus.
      if (win.scrollY !== 0) win.scrollTo(0, 0);
    } else if (current.open) {
      delete root.dataset.keyboard;
      root.style.removeProperty("--app-height");
      root.style.removeProperty("--keyboard-inset");
    }
    current = next;
  };

  apply();
  viewport.addEventListener("resize", apply);
  viewport.addEventListener("scroll", apply);
  return () => {
    viewport.removeEventListener("resize", apply);
    viewport.removeEventListener("scroll", apply);
  };
}

/** True while an on-screen keyboard is up, so Return can mean a new line. */
export function softwareKeyboardOpen(): boolean {
  return typeof document !== "undefined" && document.documentElement.dataset.keyboard === "open";
}
