/** Relays the seller's approved marketplace commands from the hosted API to Anteroom Bridge in this Chrome. */
import type { RelayedCommand, RelayedPhoto } from "../api/cloudExtension";
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
  if (command.type !== "marketplace.execute" || !Array.isArray(command.payload.photos)) return [];
  return command.payload.photos.filter((photo): photo is { id: string; mime_type: string } =>
    typeof photo?.id === "string" && typeof photo?.mime_type === "string");
}

/** What a Bridge may declare it runs for the hosted API: marketplace actions, and each named read. */
export const RELAY_CAPABILITIES = ["marketplace.actions", "vinted.choices", "sellwild.account"] as const;

/** One relay turn: wait for the next command, run it in Chrome, post the reply. False when nothing came. */
export async function relayOnce(bridge: RelayBridge, capabilities: readonly string[]): Promise<boolean> {
  const declared = RELAY_CAPABILITIES.filter((capability) => capabilities.includes(capability));
  const next = await studioFetch(`/api/browser-relay/next?capabilities=${declared.join(",")}`);
  if (next.status === 204 || !next.ok) return false;
  const command = await next.json() as RelayedCommand;
  let reply: Record<string, unknown>;
  try {
    const photos = await Promise.all(approvedPhotos(command).map((photo) => photoData(String(command.job_id), photo)));
    reply = await bridge.runMarketplaceCommand(command, photos);
  } catch {
    // Nothing reached the marketplace: the photos never got to Chrome.
    reply = { ok: false, safe_to_retry: true, error: "Could not read the listing photos. Try again." };
  }
  await studioFetch("/api/browser-relay/replies", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: command.message_id, payload: reply }),
  });
  return true;
}

/** Relay until stopped. A failed request waits a few seconds before asking again. */
export function startChromeRelay(bridge: RelayBridge, capabilities: readonly string[]): () => void {
  let active = true;
  void (async () => {
    while (active) {
      try { await relayOnce(bridge, capabilities); }
      catch { await new Promise((resolve) => window.setTimeout(resolve, 3000)); }
    }
  })();
  return () => { active = false; };
}
