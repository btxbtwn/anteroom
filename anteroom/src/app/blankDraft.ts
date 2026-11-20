/*
 * New listing makes its draft at once, so photos and fields have somewhere to
 * save. A draft the seller leaves without adding a photo or typing anything is
 * discarded, the way Apple Notes drops an empty note. The server makes the
 * final call in one transaction: a draft that holds anything is kept.
 */
import { commitUnsavedWork, hasUnsavedWork } from "../lib/unsavedWork";
import { photosUploading } from "../photoUploads";

const SETTLE_POLL_MS = 200;
/** How long to wait for the seller's last save or upload; past it the draft is kept. */
export const SETTLE_DEADLINE_MS = 30_000;

type Discard = (id: string) => Promise<{ discarded: boolean }>;

function busy(id: string) {
  return hasUnsavedWork() || photosUploading(id);
}

/**
 * Discards the draft if it is still blank once every save and upload has landed.
 * Resolves true when the server removed it. Never throws: keeping a draft is the safe outcome.
 */
export async function discardBlankDraft(
  id: string,
  discard: Discard,
  { deadlineMs = SETTLE_DEADLINE_MS, pollMs = SETTLE_POLL_MS }: { deadlineMs?: number; pollMs?: number } = {},
): Promise<boolean> {
  // A field typed into but not yet blurred saves now, so the server sees it.
  commitUnsavedWork();
  const until = Date.now() + deadlineMs;
  while (busy(id)) {
    if (Date.now() >= until) return false;
    await new Promise((resolve) => setTimeout(resolve, pollMs));
  }
  try {
    return (await discard(id)).discarded;
  } catch {
    return false;
  }
}

/**
 * The tab is closing: there is no time to wait, so discard only when nothing is
 * pending. The page's own guard has already started saving any typed field.
 */
export function discardBlankDraftOnExit(id: string, discard: Discard): boolean {
  if (busy(id)) return false;
  void discard(id).catch(() => {});
  return true;
}
