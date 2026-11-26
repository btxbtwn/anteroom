// A removed photo stays on the server while its Undo toast is open. Any request
// that could read the listing's photos (generate, marketplace draft/list/update,
// schedule, bulk listing actions) first commits those removals and waits for
// them, so it never uses a photo the seller already removed.

type PendingPhotoDelete = { convId: string; flush: () => Promise<void> };

const pending = new Set<PendingPhotoDelete>();

/** Track a photo removal waiting on Undo. Returns a function that stops tracking it. */
export function trackPendingPhotoDelete(convId: string, flush: () => Promise<void>): () => void {
  const entry: PendingPhotoDelete = { convId, flush };
  pending.add(entry);
  return () => {
    pending.delete(entry);
  };
}

/** Commit pending photo removals (for one listing, or all) and wait for them. */
async function settlePendingPhotoDeletes(convId?: string): Promise<void> {
  const due = [...pending].filter((entry) => convId === undefined || entry.convId === convId);
  await Promise.all(due.map((entry) => entry.flush().catch(() => undefined)));
}

const READ_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/**
 * Pending removals a request must wait for, or null when it need not wait.
 * Photo requests themselves and plain field saves (`PATCH /conversations/{id}`,
 * `PUT /conversations/{id}/listing`) do not wait, so Undo stays available while
 * the seller edits.
 */
export function photoDeleteGate(input: string, method: string | undefined): Promise<void> | null {
  if (pending.size === 0) return null;
  if (READ_METHODS.has((method ?? "GET").toUpperCase())) return null;
  const path = input.split(/[?#]/, 1)[0];
  const apiAt = path.indexOf("/api/");
  if (apiAt < 0) return null;
  const segments = path.slice(apiAt + "/api/".length).split("/");
  if (segments[0] === "conversations" && segments.length >= 3 && segments[1]) {
    if (segments[2] === "photos" || (segments[2] === "listing" && segments.length === 3)) return null;
    let convId: string;
    try {
      convId = decodeURIComponent(segments[1]);
    } catch {
      convId = segments[1];
    }
    return settlePendingPhotoDeletes(convId);
  }
  if (segments[0] === "listings" || (segments[0] === "automations" && segments[1] === "scheduled-drafts")) {
    return settlePendingPhotoDeletes();
  }
  return null;
}
