// Native forms use the seller's existing session. No cookie/token export.
// Single-use mutation commands are journaled; reconnects only read results.
let activeNativeOperation = null;

function nativeDraftUrl(platform, raw) {
  try {
    const url = new URL(raw);
    const valid = platform === 'mercari' ? url.origin === 'https://www.mercari.com' && /^\/sell\/draft\/[A-Za-z0-9_-]+\/$/.test(url.pathname)
      : platform === 'depop' && url.origin === 'https://www.depop.com' && /^\/sellinghub\/drafts\/edit\/[A-Za-z0-9_-]{1,80}\/?$/.test(url.pathname);
    return valid ? `${url.origin}${url.pathname}` : '';
  } catch { return ''; }
}

function nativeFrameNavigated(error) {
  return /Frame with ID \d+ was removed|No frame with id/i.test(String(error?.message || error));
}

async function nativeWait(tabId, test, deadline) {
  while (Date.now() < deadline) {
    const tab = await chrome.tabs.get(tabId);
    if (test(tab)) return tab;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error('Chrome did not confirm the marketplace navigation.');
}

async function nativePhoto(jobId, photo) {
  if (!/^[a-f0-9-]+$/i.test(String(photo.id))) throw new Error('Invalid photo ID.');
  const response = await fetch(`${STUDIO_URL}/api/jobs/${encodeURIComponent(jobId)}/photos/${encodeURIComponent(photo.id)}`);
  if (!response.ok) throw new Error('Could not load a listing photo.');
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.length > 20 * 1024 * 1024) throw new Error('A listing photo exceeds 20 MB.');
  let binary = '';
  for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return { base64: btoa(binary), name: `${photo.id}.${photo.mime_type === 'image/png' ? 'png' : 'jpg'}`, mime_type: photo.mime_type || 'image/jpeg' };
}

async function recoverNativeMarketplace(context) {
  if (context.marketplace === 'sellwild') return await runSellwildNative({ payload: context }, true);
  if (context.marketplace === 'depop') return await runDepopNative({ payload: context }, true);
  if (context.marketplace === 'poshmark') return await runPoshmarkApi({ payload: context }, true);
  if (context.marketplace === 'mercari') return await runMercariNative({ payload: context }, true);
  return { ok: false, error: 'Unsupported marketplace.' };
}

async function runNativeMarketplace(msg) {
  const p = msg.payload || {};
  if (!['poshmark', 'mercari', 'depop', 'sellwild'].includes(p.marketplace)
      || !({ sellwild: ['publish'], poshmark: ['list', 'update'] }[p.marketplace] || ['save_draft', 'publish']).includes(p.action)) {
    return { ok: false, safe_to_retry: true, error: 'Unsupported marketplace action.' };
  }
  if (p.action === 'list' && p.confirm !== true) return { ok: false, safe_to_retry: true, error: 'Confirm listing on this marketplace.' };
  if (p.action === 'update' && (p.confirm !== true || !/^[a-f0-9]{24}$/.test(String(p.listing_id || '')))) {
    return { ok: false, safe_to_retry: true, error: 'Confirm updating the live Poshmark listing.' };
  }
  if (p.action === 'publish' && (p.confirm !== true || (p.marketplace !== 'sellwild' && !nativeDraftUrl(p.marketplace, p.draft_url)))) {
    return { ok: false, safe_to_retry: true, error: 'Confirm publishing an existing native draft.' };
  }
  if (p.draft_url && !nativeDraftUrl(p.marketplace, p.draft_url)) return { ok: false, safe_to_retry: true, error: 'Invalid native draft link.' };
  if (p.marketplace === 'sellwild') return await runSellwildNative(msg);
  if (p.marketplace === 'depop') return await runDepopNative(msg);
  if (p.marketplace === 'poshmark') return await runPoshmarkApi(msg);
  return await runMercariNative(msg);
}

async function handleNativeMarketplaceMessage(msg) {
  const operationId = String(msg.payload?.operation_id || '');
  if (!/^[a-f0-9-]+$/i.test(operationId)) {
    sendBrowserResult(msg, { ok: false, safe_to_retry: true, error: 'Invalid operation ID.' });
    return;
  }
  const key = `native_operation_${operationId}`;
  let stored = (await chrome.storage.local.get(key))[key];
  if (msg.type === 'marketplace.result') {
    if (!stored && msg.payload.context?.draft_url) stored = { finished: false, context: msg.payload.context };
    if (stored && !stored.context && msg.payload.context) stored = { ...stored, context: msg.payload.context };
    if (stored?.context && !activeNativeOperation && !activeJob && !activePatch && (!stored.finished || (!stored.result?.ok && !stored.result?.safe_to_retry))) {
      activeNativeOperation = { id: operationId, tabId: null, cancelled: false };
      try {
        const recovered = await recoverNativeMarketplace({ ...stored.context, ...msg.payload.context,
          draft_url: msg.payload.context?.draft_url || stored.receipt_url || stored.context.draft_url,
          expected_photo_count: stored.expected_photo_count ?? msg.payload.context?.expected_photo_count,
          publish_job_id: stored.publish_job_id });
        if (recovered) {
          await chrome.storage.local.set({ [key]: { ...stored, finished: true, result: recovered } });
          sendBrowserResult(msg, { finished: true, result: recovered });
          return;
        }
      } finally { activeNativeOperation = null; }
    }
    sendBrowserResult(msg, stored?.finished ? { finished: true, result: stored.result } : { finished: false });
    return;
  }
  if (stored) {
    sendBrowserResult(msg, stored.finished ? stored.result : { ok: false, error: 'This action is already in progress or interrupted. Check its result.' });
    return;
  }
  if (activeNativeOperation || activeJob || activePatch) {
    sendBrowserResult(msg, { ok: false, safe_to_retry: true, error: 'Another automation job is running.' });
    return;
  }
  activeNativeOperation = { id: operationId, tabId: null, cancelled: false };
  try {
    const context = { marketplace: msg.payload.marketplace, action: msg.payload.action, draft_url: msg.payload.draft_url,
      listing_id: msg.payload.listing_id, local_draft_id: msg.payload.local_draft_id, revision_id: msg.payload.revision_id,
      operation_id: operationId };
    await chrome.storage.local.set({ [key]: { finished: false, context } });
    const result = await runNativeMarketplace(msg);
    const journal = (await chrome.storage.local.get(key))[key];
    await chrome.storage.local.set({ [key]: { ...journal, finished: true, result } });
    sendBrowserResult(msg, result);
  } finally { activeNativeOperation = null; }
}

async function cancelNativeMarketplaceOperation(jobId) {
  if (activeNativeOperation?.id !== jobId) return;
  activeNativeOperation.cancelled = true;
  const tabId = activeNativeOperation.tabId;
  if (tabId != null) {
    await chrome.scripting.executeScript({ target: { tabId }, world: 'MAIN',
      func: id => { globalThis.__anteroomCancelledNativeOperation = id; }, args: [jobId] }).catch(() => {});
  }
}
