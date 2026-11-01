// Save draft started in the web app. The hosted API signs one save_draft command;
// the page only relays it. This verifies the signature with the key the Bridge
// ships with, runs the same native save as the Mac (never a publish), and reports
// the result to the hosted API itself, so a closed tab does not lose it.
const CLOUD_ORIGIN = 'https://myanteroom.com';
const CLOUD_DRAFT_MARKETPLACES = new Set(['poshmark', 'mercari', 'depop']);
const CLOUD_RESULT_KEYS = ['ok', 'state', 'draft_url', 'draft_replaced', 'error', 'error_code', 'fields', 'retry_after_s',
  'marketplace_message', 'safe_to_retry', 'warning'];
// Operations this worker is running now, so a relayed copy of the same command cannot interfere.
const cloudDraftsRunning = new Set();

function cloudBytes(value) {
  const text = atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4));
  return Uint8Array.from(text, char => char.charCodeAt(0));
}

// The command, when a trusted key signed it for this account and it is a current save_draft. Otherwise null.
async function verifyCloudCommand(relayed, accountId, keys = CLOUD_COMMAND_KEYS, now = Date.now()) {
  const shape = CloudMarketplaceProtocol.command(relayed);
  if (!shape || !Array.isArray(keys) || !keys.length) return null;
  let signature;
  try { signature = cloudBytes(shape.signature); } catch { return null; }
  if (signature.length !== 64) return null;
  let command = null;
  for (const jwk of keys) {
    try {
      const key = await crypto.subtle.importKey('jwk', { kty: jwk.kty, crv: jwk.crv, x: jwk.x, y: jwk.y },
        { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
      if (await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, key, signature, new TextEncoder().encode(shape.body))) {
        command = JSON.parse(new TextDecoder().decode(cloudBytes(shape.body)));
        break;
      }
    } catch { /* Try the next key. */ }
  }
  const p = command?.payload;
  if (!command || command.version !== 1 || command.kind !== 'marketplace.save_draft' || command.user_id !== accountId
      || !/^[a-f0-9-]{1,64}$/i.test(String(command.operation_id)) || !/^[A-Za-z0-9_-]{43}$/.test(String(command.token))
      || !Number.isInteger(command.expires_at) || command.expires_at * 1000 <= now
      || !p || typeof p !== 'object' || !CLOUD_DRAFT_MARKETPLACES.has(p.marketplace) || p.action !== 'save_draft'
      || p.confirm !== false || p.operation_id !== command.operation_id || Object.hasOwn(p, 'video')
      || !p.listing || typeof p.listing !== 'object' || !Array.isArray(p.photos) || !p.photos.length
      || p.photos.some(photo => !/^[a-f0-9-]+$/i.test(String(photo?.id)))) return null;
  return command;
}

function cloudResult(result) {
  const out = {};
  for (const key of CLOUD_RESULT_KEYS) if (result && Object.hasOwn(result, key)) out[key] = result[key];
  if (typeof out.ok !== 'boolean') return { ok: false, safe_to_retry: false, error: 'Chrome did not finish the draft.', error_code: 'uncertain' };
  return out;
}

// Report to the hosted API with the operation's token. True once Anteroom has it.
async function reportCloudResult(command, result) {
  const body = JSON.stringify({ result: cloudResult(result) });
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (attempt) await marketplaceSleep(1000 * attempt);
    try {
      const response = await fetch(`${CLOUD_ORIGIN}/api/operations/${encodeURIComponent(command.operation_id)}/result`, {
        method: 'POST', credentials: 'omit', body,
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${command.token}` },
        signal: AbortSignal.timeout(15000),
      });
      if (response.ok) return true;
      // Used, expired or refused: retrying cannot change the answer.
      if (response.status < 500) return false;
    } catch { /* Network: try again. */ }
  }
  marketplaceLog(command.payload.marketplace, 'cloud-result', { error_code: 'network' });
  return false;
}

// A verified command that cannot run here (this tab is not connected for its account).
// Reported so the listing does not wait for the token to expire. Never for one already started.
async function refuseCloudDraft(command, error_code, error) {
  const key = `native_operation_${command.operation_id}`;
  if (cloudDraftsRunning.has(command.operation_id) || (await chrome.storage.local.get(key))[key]) return false;
  return await reportCloudResult(command, { ok: false, safe_to_retry: true, error_code, error });
}

async function runCloudSaveDraft(command) {
  const id = command.operation_id;
  if (cloudDraftsRunning.has(id)) return { ok: false, error_code: 'busy' };
  cloudDraftsRunning.add(id);
  let result;
  try {
    nativeOperationSources.set(id, { origin: CLOUD_ORIGIN, token: command.token });
    const key = `native_operation_${id}`;
    const stored = (await chrome.storage.local.get(key))[key];
    // Save draft only, whatever else the payload says: this path never publishes.
    result = await executeNativeOperation({ type: 'marketplace.execute', job_id: id,
      payload: { ...command.payload, action: 'save_draft', confirm: false, operation_id: id } }, stored);
  } catch (error) {
    result = marketplaceFailure(error, false);
  } finally {
    nativeOperationSources.delete(id);
  }
  try {
    const recorded = await reportCloudResult(command, result);
    return { ...cloudResult(result), recorded };
  } finally {
    cloudDraftsRunning.delete(id);
  }
}

const cloudDrafts = {
  verify: (relayed, accountId) => verifyCloudCommand(relayed, accountId),
  refuse: refuseCloudDraft,
  run: runCloudSaveDraft,
  available: () => Array.isArray(CLOUD_COMMAND_KEYS) && CLOUD_COMMAND_KEYS.length > 0,
};
