/**
 * After a deploy, the page still running the previous build asks for code
 * files that no longer exist. Vite reports that as `vite:preloadError`; one
 * reload fetches the new build. A second failure soon after is left to the
 * error boundary, so a broken deploy cannot reload in a loop.
 */
const RELOADED_AT_KEY = "anteroom.chunk-reload-at";
/** A failure this long after the last reload is a new deploy, not the same one. */
const RELOAD_WINDOW_MS = 60_000;

type SessionStore = Pick<Storage, "getItem" | "setItem">;

/** Whether to reload now, recording the attempt. Storage may throw (private mode, blocked site data). */
export function shouldReloadForChunk(storage: SessionStore | null, now = Date.now()): boolean {
  try {
    if (!storage) return false;
    const last = Number(storage.getItem(RELOADED_AT_KEY) || 0);
    if (last && now - last < RELOAD_WINDOW_MS) return false;
    storage.setItem(RELOADED_AT_KEY, String(now));
    return true;
  } catch {
    return false;
  }
}

function sessionStore(): SessionStore | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function installChunkReload(target: Window = window): () => void {
  const onPreloadError = (event: Event) => {
    if (!shouldReloadForChunk(sessionStore())) return;
    // Handled: stop Vite from rethrowing while the page reloads.
    event.preventDefault();
    target.location.reload();
  };
  target.addEventListener("vite:preloadError", onPreloadError);
  return () => target.removeEventListener("vite:preloadError", onPreloadError);
}

/** A failed dynamic import: the code for a screen could not be fetched. */
export function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /dynamically imported module|Importing a module script failed|error loading dynamically imported|Unable to preload CSS/i.test(message);
}
