// Seller-confirmed delisting from the extension worker, with no marketplace tab.
// Each step reads the listing first: one already gone counts as deleted, so a
// delete whose reply was lost is safe to repeat. Requests come from each
// marketplace's own web client (read 2026-10-24).
const DELETE_DEADLINE_MS = 60000;

function deleteGuard(deadline) {
  if (Date.now() >= deadline || activeNativeOperation?.cancelled) throw new Error('The delete stopped. Delist again to check; it is safe to repeat.');
}

async function deletePoshmark(id, deadline) {
  const cookie = await chrome.cookies.get({ url: 'https://poshmark.com/', name: 'jwt' });
  let account = {};
  try { account = JSON.parse(atob(cookie.value.split('.')[1])); } catch {}
  if (!/^[a-f0-9]{24}$/.test(String(account.user_id))) throw new Error('Reconnect Poshmark in Anteroom first.');
  const call = async method => {
    deleteGuard(deadline);
    const response = await fetch(`https://poshmark.com/vm-rest/posts/${id}?app_version=2.55&app_type=web&pm_version=2026.43.00`, {
      method, credentials: 'include', headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(25000) });
    if (!response.ok) throw new Error(`Poshmark rejected the ${method === 'GET' ? 'read' : 'delete'} (HTTP ${response.status}).`);
    return response.json();
  };
  // Poshmark answers errors with HTTP 200 and an error body.
  const gone = result => ['PostRemovedError', 'NotFoundError'].includes(result?.error?.errorType);
  const before = await call('GET');
  if (gone(before)) return { ok: true, state: 'deleted', already: true };
  if (before.error) throw new Error('Poshmark did not return this listing.');
  const post = before.data || before;
  if (String(post.creator_id) !== String(account.user_id)) throw new Error('This Poshmark listing belongs to another account.');
  const sold = (post.inventory?.size_quantities || []).some(entry => Number(entry.quantity_sold) > 0);
  if (sold || post.status === 'archived' || ['sold_out', 'not_available'].includes(post.inventory?.status)) {
    throw new Error('This Poshmark listing has sold. It was not deleted.');
  }
  const removed = await call('DELETE');
  if (removed?.error) throw new Error(`Poshmark refused the delete${removed.error.userMessage ? `: ${String(removed.error.userMessage).slice(0, 160)}` : '.'}`);
  if (!gone(await call('GET'))) throw new Error('Poshmark has not confirmed the delete. Delist again to check.');
  return { ok: true, state: 'deleted' };
}

async function deleteMercari(id, deadline) {
  deleteGuard(deadline);
  // Mercari's own Delete button: updateItemStatus to "cancel". Its reply
  // carries the new status; an item already removed reports the same.
  const data = await mercariRequest({ operationName: 'UpdateItemStatusMutation', variables: { input: { id, status: 'cancel' } },
    extensions: { persistedQuery: { version: 1, sha256Hash: '55bd4e7d2bc2936638e1451da3231e484993635d7603431d1a2978e3d59656f8' } } },
  { deadline, operationName: 'UpdateItemStatusMutation' });
  const status = String(data.updateItemStatus?.status || '');
  if (!['cancel', 'admin_cancel'].includes(status)) throw new Error(`Mercari did not confirm the delete${status ? ` (status ${status})` : ''}.`);
  return { ok: true, state: 'deleted' };
}

async function deleteDepop(slug, deadline) {
  const cookie = await chrome.cookies.get({ url: 'https://www.depop.com/', name: 'access_token' });
  if (!cookie?.value) throw new Error('Reconnect Depop in Anteroom first.');
  const call = async (method, path) => {
    deleteGuard(deadline);
    await ensureMarketplaceRequestContext();
    const response = await fetch(`https://webapi.depop.com${path}`, { method, credentials: 'omit', signal: AbortSignal.timeout(25000),
      headers: { Accept: 'application/json', Authorization: `Bearer ${decodeURIComponent(cookie.value)}` } });
    if (method === 'GET' && response.status === 404) return null;
    if (response.status === 403) throw new Error('Depop requested browser verification. Reconnect Depop in Anteroom.');
    if (!response.ok) throw new Error(`Depop rejected the ${method === 'GET' ? 'read' : 'delete'} (HTTP ${response.status}).`);
    return response.status === 204 ? {} : response.json().catch(() => ({}));
  };
  const product = await call('GET', `/presentation/api/v1/products/by-slug/${encodeURIComponent(slug)}/`);
  const status = String(product?.status || '').toUpperCase();
  if (!product || status.includes('DELETED')) return { ok: true, state: 'deleted', already: true };
  if (/SOLD|PURCHASED/.test(status) || product.sold === true) throw new Error('This Depop listing has sold. It was not deleted.');
  const id = String(product.id || '');
  if (!/^\d{1,15}$/.test(id)) throw new Error('Depop did not return this listing\'s ID.');
  await call('DELETE', `/presentation/api/v1/products/${id}/`);
  const after = await call('GET', `/presentation/api/v1/products/by-slug/${encodeURIComponent(slug)}/`);
  if (after && !String(after.status || '').toUpperCase().includes('DELETED')) throw new Error('Depop has not confirmed the delete. Delist again to check.');
  return { ok: true, state: 'deleted' };
}

async function deleteSellwild(id, deadline) {
  deleteGuard(deadline);
  // Sellwild gives sellers delist, not delete; a delisted item leaves the site.
  const { response, result } = await sellwildFetch('/api/seller/listings/bulk-status', { method: 'POST', deadline,
    body: { action: 'delist', listingIds: [Number(id)] } });
  if (!response.ok || result?.success !== true) throw new Error(`Sellwild did not delist the item${result?.error ? `: ${String(result.error).slice(0, 160)}` : '.'}`);
  if (result.changed > 0) return { ok: true, state: 'deleted' };
  if (result.notFound > 0) throw new Error('Sellwild says this listing is not on your account.');
  if (result.skipped > 0) {
    // Already delisted, or a sale is in progress: Sellwild skips both.
    throw new Error('Sellwild skipped this listing: it is already delisted or has a sale in progress. Check it on Sellwild.');
  }
  throw new Error('Sellwild did not confirm the delist.');
}

const MARKETPLACE_DELETE = {
  poshmark: [/^[a-f0-9]{24}$/, deletePoshmark],
  mercari: [/^m\d{6,}$/, deleteMercari],
  depop: [/^[a-z0-9][a-z0-9-]{0,200}$/, deleteDepop],
  sellwild: [/^\d{1,15}$/, deleteSellwild],
};

async function runMarketplaceDelete(payload) {
  const entry = MARKETPLACE_DELETE[payload?.marketplace];
  if (!entry || payload.confirm !== true || !entry[0].test(String(payload.listing_id || ''))) {
    return { ok: false, error: 'Confirm deleting a named marketplace listing.' };
  }
  try {
    return await entry[1](String(payload.listing_id), Date.now() + DELETE_DEADLINE_MS);
  } catch (error) {
    return { ok: false, error: String(error.message || error) };
  }
}

async function handleMarketplaceDelete(msg) {
  if (activeNativeOperation || activeJob || activePatch) {
    sendBrowserResult(msg, { ok: false, error: 'Another automation job is running.' });
    return;
  }
  activeNativeOperation = { id: `delete-${msg.payload?.request_id || ''}`, tabId: null, cancelled: false };
  try { sendBrowserResult(msg, await runMarketplaceDelete(msg.payload)); }
  finally { activeNativeOperation = null; }
}
