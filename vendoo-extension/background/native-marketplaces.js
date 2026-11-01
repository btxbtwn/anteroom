// Native forms use the seller's existing session. No cookie/token export.
// Single-use mutation commands are journaled; reconnects only read results.
// A listing action holds only its own marketplace, so different marketplaces run
// side by side. Delist and promotions hold every marketplace ('*').
const nativeOperations = new Map();

function nativeOperationFor(marketplace) {
  return nativeOperations.get(marketplace) || nativeOperations.get('*') || null;
}

function nativeOperationsRunning() {
  return nativeOperations.size > 0;
}

// Returns the claimed operation, or null when Chrome is busy for that scope.
function claimNativeOperation(scope, id) {
  if (activeJob || activePatch || nativeOperations.has('*') || (scope === '*' ? nativeOperations.size > 0 : nativeOperations.has(scope))) return null;
  const operation = { id, scope, tabId: null, cancelled: false, controller: new AbortController() };
  nativeOperations.set(scope, operation);
  return operation;
}

function releaseNativeOperation(operation) {
  if (operation && nativeOperations.get(operation.scope) === operation) nativeOperations.delete(operation.scope);
}

function nativeDraftUrl(platform, raw) {
  try {
    const url = new URL(raw);
    const valid = platform === 'mercari' ? url.origin === 'https://www.mercari.com' && /^\/sell\/draft\/[A-Za-z0-9_-]+\/$/.test(url.pathname)
      : platform === 'depop' ? url.origin === 'https://www.depop.com' && /^\/sellinghub\/drafts\/edit\/[A-Za-z0-9_-]{1,80}\/?$/.test(url.pathname)
      : platform === 'poshmark' ? url.origin === 'https://poshmark.com' && /^\/edit-listing\/[a-f0-9]{24}$/.test(url.pathname) && !url.search && !url.hash
      : platform === 'vinted' && url.origin === 'https://www.vinted.com' && /^\/items\/[1-9]\d{0,14}\/edit\/?$/.test(url.pathname);
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

// Binary video delivery is scoped to the job's selected immutable revision.
async function nativeVideo(jobId, video, deadline) {
  if (!/^[a-f0-9-]+$/i.test(String(video.id))) throw marketplaceError('Invalid video ID.');
  let response;
  try {
    response = await fetch(`${STUDIO_URL}/api/jobs/${encodeURIComponent(jobId)}/videos/${encodeURIComponent(video.id)}`,
      { signal: AbortSignal.timeout(Math.max(1000, Math.min(60000, deadline - Date.now()))) });
  } catch {
    throw marketplaceError('Anteroom could not load the selected video in time.', { code: 'network' });
  }
  if (!response.ok) throw marketplaceError('Could not load the video selected for this revision.', { code: 'network' });
  const blob = await response.blob();
  if (blob.size !== video.size_bytes || !blob.type.startsWith('video/')) throw marketplaceError('The listing video changed. Choose it again before listing.');
  return blob;
}

// Photos the hosted Anteroom page read for a relayed job, by job and photo id.
// The web app has no loopback server for the Bridge to read them from.
const relayedPhotos = new Map();

async function nativePhoto(jobId, photo, deadline = Date.now() + 30000) {
  if (!/^[a-f0-9-]+$/i.test(String(photo.id))) throw marketplaceError('Invalid photo ID.');
  const relayed = relayedPhotos.get(String(jobId))?.get(String(photo.id));
  if (relayed) return { ...relayed, name: `${photo.id}.${relayed.mime_type === 'image/png' ? 'png' : 'jpg'}` };
  let response;
  try {
    response = await fetch(`${STUDIO_URL}/api/jobs/${encodeURIComponent(jobId)}/photos/${encodeURIComponent(photo.id)}`,
      { signal: AbortSignal.timeout(Math.max(1000, Math.min(30000, deadline - Date.now()))) });
  } catch {
    throw marketplaceError('Anteroom could not load a listing photo in time.', { code: 'network' });
  }
  if (!response.ok) throw marketplaceError('Could not load a listing photo.', { code: 'network' });
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.length > 20 * 1024 * 1024) throw marketplaceMissing('', [{ field: 'photos', needs: 'photos under 20 MB each' }]);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return { base64: btoa(binary), name: `${photo.id}.${photo.mime_type === 'image/png' ? 'png' : 'jpg'}`, mime_type: photo.mime_type || 'image/jpeg' };
}

function nativeRefusal(error) {
  return marketplaceFailure(marketplaceError(error), true);
}

async function recoverNativeMarketplace(context) {
  if (context.marketplace === 'vinted') return await runVintedNative({ payload: context }, true);
  if (context.marketplace === 'sellwild') return await runSellwildNative({ payload: context }, true);
  if (context.marketplace === 'depop') return await runDepopNative({ payload: context }, true);
  if (context.marketplace === 'poshmark') return await runPoshmarkApi({ payload: context }, true);
  if (context.marketplace === 'mercari') return await runMercariNative({ payload: context }, true);
  return nativeRefusal('Unsupported marketplace.');
}

// The live listing an Update writes onto, by each marketplace's own listing id.
const NATIVE_UPDATE_LISTING = {
  poshmark: ['Poshmark', /^[a-f0-9]{24}$/],
  depop: ['Depop', /^[a-z0-9][a-z0-9-]{0,200}$/i],
  mercari: ['Mercari', /^m\d{6,}$/],
  vinted: ['Vinted', /^[1-9]\d{0,14}$/],
  sellwild: ['Sellwild', /^[1-9]\d{0,14}$/],
};

async function runNativeMarketplace(msg) {
  const p = msg.payload || {};
  if (!['poshmark', 'mercari', 'depop', 'sellwild', 'vinted'].includes(p.marketplace)
      || !({ sellwild: ['publish', 'update'], poshmark: ['list', 'update', 'save_draft'] }[p.marketplace] || ['save_draft', 'publish', 'update']).includes(p.action)) {
    return nativeRefusal('Unsupported marketplace action.');
  }
  if (p.action === 'list' && p.confirm !== true) return nativeRefusal('Confirm listing on this marketplace.');
  if (p.action === 'update') {
    const [name, pattern] = NATIVE_UPDATE_LISTING[p.marketplace];
    if (p.confirm !== true || !pattern.test(String(p.listing_id || ''))) return nativeRefusal(`Confirm updating the live ${name} listing.`);
  }
  if (p.action === 'publish' && (p.confirm !== true || (p.marketplace !== 'sellwild' && !nativeDraftUrl(p.marketplace, p.draft_url)))) {
    return nativeRefusal('Confirm publishing an existing native draft.');
  }
  if (p.draft_url && !nativeDraftUrl(p.marketplace, p.draft_url)) return nativeRefusal('Invalid native draft link.');
  if (p.marketplace === 'vinted') return await runVintedNative(msg);
  if (p.marketplace === 'sellwild') return await runSellwildNative(msg);
  if (p.marketplace === 'depop') return await runDepopNative(msg);
  if (p.marketplace === 'poshmark') return await runPoshmarkApi(msg);
  return await runMercariNative(msg);
}

async function handleNativeMarketplaceMessage(msg, reply = result => sendBrowserResult(msg, result)) {
  const operationId = String(msg.payload?.operation_id || '');
  if (!/^[a-f0-9-]+$/i.test(operationId)) {
    reply(nativeRefusal('Invalid operation ID.'));
    return;
  }
  const key = `native_operation_${operationId}`;
  let stored = (await chrome.storage.local.get(key))[key];
  if (msg.type === 'marketplace.result') {
    if (!stored && msg.payload.context?.draft_url) stored = { finished: false, context: msg.payload.context };
    if (stored && !stored.context && msg.payload.context) stored = { ...stored, context: msg.payload.context };
    const recovery = stored?.context && (!stored.finished || (!stored.result?.ok && !stored.result?.safe_to_retry))
      ? claimNativeOperation(stored.context.marketplace, operationId) : null;
    if (recovery) {
      try {
        const recovered = await recoverNativeMarketplace({ ...stored.context, ...msg.payload.context,
          draft_url: msg.payload.context?.draft_url || stored.receipt_url || stored.context.draft_url,
          expected_photo_count: stored.expected_photo_count ?? msg.payload.context?.expected_photo_count,
          publish_job_id: stored.publish_job_id });
        if (recovered) {
          await chrome.storage.local.set({ [key]: { ...stored, finished: true, result: recovered } });
          reply({ finished: true, result: recovered });
          return;
        }
      } finally { releaseNativeOperation(recovery); }
    }
    // A finished Depop publish used to keep only the draft link. Read the public product before answering.
    if (stored?.finished && stored.result?.ok && stored.result.state === 'published' && stored.context?.marketplace === 'depop'
        && !stored.result.listing_url && stored.publish_job_id) {
      const reading = claimNativeOperation('depop', operationId);
      if (reading) try {
        const status = await depopApiAction('publish_status', {
          ...stored.context, ...msg.payload.context, publish_job_id: stored.publish_job_id,
          action: 'publish', deadline: Date.now() + 50000,
        });
        if (status?.listing_url) {
          stored = { ...stored, result: { ...stored.result, listing_url: status.listing_url } };
          await chrome.storage.local.set({ [key]: stored });
        }
      } catch { /* The listing is already live. A missing link stays missing. */ }
      finally { releaseNativeOperation(reading); }
    }
    reply(stored?.finished ? { finished: true, result: stored.result } : { finished: false });
    return;
  }
  if (stored) {
    reply(stored.finished ? stored.result
      : marketplaceFailure(new Error('This action is already in progress or interrupted. Check its result.'), false));
    return;
  }
  const marketplace = msg.payload.marketplace;
  const operation = claimNativeOperation(marketplace, operationId);
  if (!operation) {
    reply(nativeRefusal(activeJob || activePatch || nativeOperations.has('*')
      ? 'Another automation job is running.' : `Another ${marketplaceLabel(marketplace)} action is running.`));
    return;
  }
  try {
    const context = { marketplace: msg.payload.marketplace, action: msg.payload.action, draft_url: msg.payload.draft_url,
      listing_id: msg.payload.listing_id, local_draft_id: msg.payload.local_draft_id, revision_id: msg.payload.revision_id,
      operation_id: operationId };
    await chrome.storage.local.set({ [key]: { finished: false, context } });
    const result = await runNativeMarketplace(msg);
    const journal = (await chrome.storage.local.get(key))[key];
    await chrome.storage.local.set({ [key]: { ...journal, finished: true, result } });
    reply(result);
  } finally { releaseNativeOperation(operation); }
}

async function cancelNativeMarketplaceOperation(jobId) {
  const operation = [...nativeOperations.values()].find(candidate => candidate.id === jobId);
  if (!operation) return;
  operation.cancelled = true;
  operation.controller?.abort();
  const tabId = operation.tabId;
  if (tabId != null) {
    await chrome.scripting.executeScript({ target: { tabId }, world: 'MAIN',
      func: id => { globalThis.__anteroomCancelledNativeOperation = id; }, args: [jobId] }).catch(() => {});
  }
}
