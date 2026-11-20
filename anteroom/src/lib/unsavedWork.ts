/**
 * Edits that have not reached the server yet: a field the seller is typing in
 * (saved on blur) and a listing save that is queued or in flight. Leaving the
 * page, or reloading it for an update, would drop them.
 */
type UnsavedSource = {
  /** True while this source holds work the server does not have. */
  pending: () => boolean;
  /** Starts saving what it can, e.g. a field typed but not yet blurred. */
  commit?: () => void;
};

const sources = new Set<UnsavedSource>();
const listeners = new Set<() => void>();
let snapshot = false;

function refresh() {
  const next = [...sources].some((source) => source.pending());
  if (next === snapshot) return;
  snapshot = next;
  listeners.forEach((listener) => listener());
}

/** Registers a source; call the result to remove it. */
export function registerUnsavedWork(source: UnsavedSource): () => void {
  sources.add(source);
  refresh();
  return () => {
    sources.delete(source);
    refresh();
  };
}

/** A source calls this whenever its pending state may have changed. */
export const notifyUnsavedWork = refresh;

export function hasUnsavedWork(): boolean {
  return snapshot;
}

export function subscribeUnsavedWork(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Saves typed-but-uncommitted fields now, so they are not lost on the way out. */
export function commitUnsavedWork(): void {
  for (const source of sources) source.commit?.();
  refresh();
}

/**
 * Commits typed fields when the page is hidden or closed, and asks the browser
 * to confirm leaving while a save is still pending.
 */
export function installUnsavedWorkGuard(target: Pick<Window, "addEventListener" | "removeEventListener">): () => void {
  const onBeforeUnload = (event: BeforeUnloadEvent) => {
    commitUnsavedWork();
    if (!hasUnsavedWork()) return;
    event.preventDefault();
    // Older WebKit shows the prompt only when returnValue is set.
    event.returnValue = "";
  };
  const onPageHide = () => commitUnsavedWork();
  target.addEventListener("beforeunload", onBeforeUnload);
  target.addEventListener("pagehide", onPageHide);
  return () => {
    target.removeEventListener("beforeunload", onBeforeUnload);
    target.removeEventListener("pagehide", onPageHide);
  };
}
