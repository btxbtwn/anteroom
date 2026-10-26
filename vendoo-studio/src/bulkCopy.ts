import type { BatchCopyPreviewRow, CopyListingRequest } from "./api/listingCopy";

export interface CopyDraft { title: string; sku: string; quantity: string; includePhotos: boolean; includeCost: boolean; skip: boolean }
export interface CopyTask { sourceId: string; title: string; request: CopyListingRequest; state: "pending" | "saved" | "failed"; targetId?: string; error?: string }
export function copyDraft(row: BatchCopyPreviewRow): CopyDraft {
  return { title: row.title || "", sku: "", quantity: "1", includePhotos: true, includeCost: false, skip: Boolean(row.skip_reason) };
}
export function copyDraftError(row: BatchCopyPreviewRow, draft: CopyDraft): string | null {
  if (draft.skip || row.skip_reason) return null;
  if (!draft.title.trim() || draft.title.trim().length > 200) return "Enter an inventory name up to 200 characters.";
  if (draft.sku.trim().length > 120) return "SKU may have at most 120 characters.";
  if (!/^\d+$/.test(draft.quantity) || Number(draft.quantity) < 1 || Number(draft.quantity) > 1000000) return "Enter a whole quantity from 1 to 1000000.";
  if (draft.includePhotos && (row.photo_count || 0) > 20) return "Copy without photos; the source has more than 20.";
  return null;
}
export function approvedCopies(rows: BatchCopyPreviewRow[], edits: ReadonlyMap<string, CopyDraft>, requestId = () => crypto.randomUUID().replace(/-/g, "")): CopyTask[] {
  return rows.flatMap((row) => {
    const draft = edits.get(row.source_id) || copyDraft(row);
    if (draft.skip || row.skip_reason) return [];
    const error = copyDraftError(row, draft);
    if (error || !row.expected_basis) throw new Error(error || "Reload the source review.");
    return [{ sourceId: row.source_id, title: draft.title.trim(), state: "pending" as const, request: {
      request_id: requestId(), expected_basis: row.expected_basis, title: draft.title.trim(), sku: draft.sku.trim(), quantity: Number(draft.quantity),
      include_photos: draft.includePhotos, include_cost: draft.includeCost,
    } }];
  });
}
interface CopyApi {
  copy: (id: string, body: CopyListingRequest) => Promise<{id: string}>;
  copyResult: (id: string, requestId: string) => Promise<{id: string}>;
}
/** One request at a time. Explicit retries keep the approval and request IDs. */
export async function runCopies(tasks: readonly CopyTask[], api: CopyApi, stopped: () => boolean, progress: (tasks: CopyTask[], activeId: string | null) => void): Promise<CopyTask[]> {
  let result = tasks.map((task) => ({ ...task }));
  for (const task of result) {
    if (stopped()) break;
    if (task.state === "saved") continue;
    progress(result, task.sourceId);
    let targetId: string | undefined, error: string | undefined;
    try {
      targetId = (await api.copy(task.sourceId, task.request)).id;
    } catch (failure) {
      try { targetId = (await api.copyResult(task.sourceId, task.request.request_id)).id; }
      catch { error = failure instanceof Error ? failure.message : "Copy result could not be confirmed."; }
    }
    result = result.map((current) => current.sourceId === task.sourceId ? { ...current, state: targetId ? "saved" : "failed", targetId, error } : current);
    progress(result, null);
  }
  return result;
}
