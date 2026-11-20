/** Relays the seller's approved marketplace commands from the hosted API to Anteroom Bridge in this Chrome. */
import type { CloudConnection, RelayedCommand, RelayedPhoto } from "../api/cloudExtension";
import { studioFetch } from "../api/transport";

export interface RelayBridge {
  runMarketplaceCommand: (command: RelayedCommand, photos: RelayedPhoto[]) => Promise<Record<string, unknown>>;
}

async function photoData(jobId: string, photo: { id: string; mime_type: string }): Promise<RelayedPhoto> {
  const response = await studioFetch(`/api/browser-relay/jobs/${encodeURIComponent(jobId)}/photos/${encodeURIComponent(photo.id)}`);
  if (!response.ok) throw new Error("Could not read a listing photo.");
  const bytes = new Uint8Array(await response.arrayBuffer());
  let binary = "";
  for (let index = 0; index < bytes.length; index += 32768) binary += String.fromCharCode(...bytes.subarray(index, index + 32768));
  return { id: photo.id, mime_type: photo.mime_type, base64: btoa(binary) };
}

function approvedPhotos(command: RelayedCommand): { id: string; mime_type: string }[] {
  if (command.type === "job.vendoo_api" && Array.isArray(command.payload.ops)) {
    return command.payload.ops.filter(op => op?.op === "upload_photo").map(op => op.photo);
  }
  if (command.type !== "marketplace.execute" || !Array.isArray(command.payload.photos)) return [];
  return command.payload.photos.filter((photo): photo is { id: string; mime_type: string } =>
    typeof photo?.id === "string" && typeof photo?.mime_type === "string");
}

/** What a Bridge may declare it runs for the hosted API: marketplace actions, each named read,
 * and checking the server's signed approval on every write. */
export const RELAY_CAPABILITIES = ["marketplace.actions", "marketplace.sessions", "marketplace.sales_history", "marketplace.promotions", "marketplace.import", "vendoo.drafts", "marketplace.signed_approvals", "marketplace.signed_commands"] as const;

async function nextCommand(capabilities: readonly string[]): Promise<RelayedCommand | null> {
  const declared = RELAY_CAPABILITIES.filter((capability) => capabilities.includes(capability));
  const next = await studioFetch(`/api/browser-relay/next?capabilities=${declared.join(",")}`);
  // 204: the server waited and nothing came. Any other failure waits before asking again.
  if (next.status === 204) return null;
  if (!next.ok) throw new Error(`The relay answered ${next.status}.`);
  return await next.json() as RelayedCommand;
}

async function answer(bridge: RelayBridge, command: RelayedCommand): Promise<void> {
  let reply: Record<string, unknown>;
  // Where a command's time went in this tab, kept on the job so a slow listing can be read later.
  const timing = { photos_ms: 0, photo_bytes: 0, chrome_ms: 0 };
  let sent = false;
  try {
    const started = performance.now();
    const photos = await Promise.all(approvedPhotos(command).map((photo) => photoData(String(command.job_id), photo)));
    timing.photos_ms = Math.round(performance.now() - started);
    timing.photo_bytes = photos.reduce((total, photo) => total + photo.base64.length, 0);
    const running = performance.now();
    sent = true;
    reply = await bridge.runMarketplaceCommand(command, photos);
    timing.chrome_ms = Math.round(performance.now() - running);
  } catch {
    // Before Chrome had the command nothing reached the marketplace; after, the outcome is unknown.
    reply = sent ? { ok: false, safe_to_retry: false, error: "Chrome did not finish this action.", error_code: "uncertain" }
      : { ok: false, safe_to_retry: true, error: "Could not read the listing photos. Try again." };
  }
  await studioFetch("/api/browser-relay/replies", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: command.message_id, payload: reply, timing }),
  });
}

/** One relay turn: wait for the next command, run it in Chrome, post the reply. False when nothing came. */
export async function relayOnce(bridge: RelayBridge, capabilities: readonly string[]): Promise<boolean> {
  const command = await nextCommand(capabilities).catch(() => null);
  if (command === null) return false;
  await answer(bridge, command);
  return true;
}

/** Commands Chrome may run at once. Listings on different marketplaces run side by side, as on the server. */
export const MAX_RUNNING_COMMANDS = 8;

/** Relay until stopped. A failed request waits a few seconds before asking again. */
export function startChromeRelay(bridge: RelayBridge, capabilities: readonly string[]): () => void {
  let active = true;
  let beating = false;
  const heartbeat = async () => {
    if (!active || beating) return;
    beating = true;
    try {
      await studioFetch("/api/browser-relay/heartbeat", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ capabilities: RELAY_CAPABILITIES.filter(capability => capabilities.includes(capability)) }) });
    } catch { /* The relay loop retries network failures; presence expires without another beat. */ }
    finally { beating = false; }
  };
  void heartbeat();
  const interval = window.setInterval(() => { void heartbeat(); }, 10000);
  // Each command runs while the relay asks for the next, so one long listing never holds up
  // another marketplace's command, a result check or a Cancel past the server's wait.
  const running = new Set<Promise<void>>();
  void (async () => {
    while (active) {
      if (running.size >= MAX_RUNNING_COMMANDS) { await Promise.race(running); continue; }
      try {
        const command = await nextCommand(capabilities);
        if (command === null || !active) continue;
        const work: Promise<void> = answer(bridge, command).catch(() => undefined).finally(() => { running.delete(work); });
        running.add(work);
      } catch { await new Promise((resolve) => window.setTimeout(resolve, 3000)); }
    }
  })();
  return () => { active = false; window.clearInterval(interval); };
}

/** Status checks in a row that may fail before the relay stops. */
export const MISSES_BEFORE_STOP = 2;

/**
 * What the relay carries after one status check: the capabilities to relay (empty to stop)
 * and the failed checks in a row. A check that times out while Chrome is busy filling a
 * form keeps the relay, so a running marketplace action is not cut off.
 */
export function relayAfterCheck(status: CloudConnection, relayed: string, missed: number): { relayed: string; missed: number } {
  if (status.ok && status.state === "connected") return { relayed: (status.supportedActions ?? []).join(","), missed: 0 };
  if (status.state !== "unavailable") return { relayed: "", missed: 0 };
  return missed + 1 < MISSES_BEFORE_STOP ? { relayed, missed: missed + 1 } : { relayed: "", missed: missed + 1 };
}
