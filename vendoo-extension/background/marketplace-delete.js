// Seller-confirmed delisting from the extension worker, with no marketplace tab.
// Each step reads the listing first: one already gone counts as deleted, so a
// delete whose reply was lost is safe to repeat. Requests come from each
// marketplace's own web client (read 2026-10-24; Vinted's delete on 2026-10-26).
const DELETE_DEADLINE_MS = 60000;

async function deletePoshmark(id, deadline) {
  const account = await poshmarkAccount();
  const client = poshmarkClient(deadline, await poshmarkCsrf(deadline));
  // Poshmark answers errors with HTTP 200 and an error body.
  const gone = error => ['PostRemovedError', 'NotFoundError'].includes(error?.marketplace_type);
  const read = async step => {
    try { return await client.call('GET', `/vm-rest/posts/${id}?app_type=web`, undefined, { step, what: 'the read' }); }
    catch (error) { if (gone(error)) return null; throw error; }
  };
  const post = await read('read');
  if (!post) return { ok: true, state: 'deleted', already: true };
  if (String(post.creator_id) !== account.user_id) throw marketplaceError('This Poshmark listing belongs to another account.');
  const sold = (post.inventory?.size_quantities || []).some(entry => Number(entry.quantity_sold) > 0);
  if (sold || post.status === 'archived' || ['sold_out', 'not_available'].includes(post.inventory?.status)) {
    throw marketplaceError('This Poshmark listing has sold. It was not deleted.');
  }
  await client.call('DELETE', `/vm-rest/posts/${id}?app_type=web`, undefined, { step: 'delete', what: 'the delete' });
  if (await read('read-back')) throw marketplaceError('Poshmark has not confirmed the delete. Delist again to check.', { code: 'uncertain', rejected: false });
  return { ok: true, state: 'deleted' };
}

async function deleteMercari(id, deadline) {
  mercariSession = null;
  // Mercari's own Delete button: updateItemStatus to "cancel". Its reply
  // carries the new status; an item already removed reports the same.
  const data = await mercariRequest({ operationName: 'UpdateItemStatusMutation', variables: { input: { id, status: 'cancel' } },
    extensions: { persistedQuery: { version: 1, sha256Hash: '55bd4e7d2bc2936638e1451da3231e484993635d7603431d1a2978e3d59656f8' } } },
  { deadline, operationName: 'UpdateItemStatusMutation' });
  // cancel / admin_cancel is the item status once it is gone. A delete that
  // just succeeded answers status OK instead of repeating the item status.
  const status = String(data.updateItemStatus?.status || '');
  if (!['cancel', 'admin_cancel'].includes(status) && status.toUpperCase() !== 'OK') {
    throw marketplaceError('Mercari did not confirm the delete.', { marketplace_message: status ? `status ${status}` : '' });
  }
  return { ok: true, state: 'deleted' };
}

async function deleteDepop(slug, deadline) {
  const cookie = await chrome.cookies.get({ url: 'https://www.depop.com/', name: 'access_token' });
  if (!cookie?.value) throw marketplaceError('Reconnect Depop in Anteroom first.', { code: 'signed_out' });
  const call = async (method, path, step) => {
    try {
      const { data } = await marketplaceFetch(`https://webapi.depop.com${path}`, { marketplace: 'depop', step, method, credentials: 'omit', deadline,
        headers: { Accept: 'application/json', Authorization: `Bearer ${decodeURIComponent(cookie.value)}` } });
      return data;
    } catch (error) {
      if (method === 'GET' && error.status === 404) return null;
      throw error;
    }
  };
  const product = await call('GET', `/presentation/api/v1/products/by-slug/${encodeURIComponent(slug)}/`, 'read');
  const status = String(product?.status || '').toUpperCase();
  if (!product || status.includes('DELETED')) return { ok: true, state: 'deleted', already: true };
  if (/SOLD|PURCHASED/.test(status) || product.sold === true) throw marketplaceError('This Depop listing has sold. It was not deleted.');
  const id = String(product.id || '');
  if (!/^\d{1,15}$/.test(id)) throw marketplaceError('Depop did not return this listing\'s ID.', { code: 'schema_drift' });
  await call('DELETE', `/presentation/api/v1/products/${id}/`, 'delete');
  // Depop's read lags the delete: the slug can still look on sale for a moment.
  const removed = product => !product || String(product.status || '').toUpperCase().includes('DELETED');
  let after = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    after = await call('GET', `/presentation/api/v1/products/by-slug/${encodeURIComponent(slug)}/`, 'read-back');
    if (removed(after)) return { ok: true, state: 'deleted' };
    if (attempt < 2) await marketplaceSleep(700);
  }
  const left = String(after?.status || '').toUpperCase().replace(/[^A-Z0-9_]/g, '').slice(0, 40);
  throw marketplaceError('Depop has not confirmed the delete. Delist again to check.', { code: 'uncertain', rejected: false,
    marketplace_message: left ? `status ${left}` : '' });
}

function vintedCsrfToken(html) {
  const match = String(html || '').match(/"CSRF_TOKEN"\s*:\s*"([^"]+)"/);
  const token = match ? match[1] : '';
  if (!token || token.length > 200 || /[\s\u0000-\u001f]/.test(token)) return '';
  return token;
}

function vintedAccountId(body) {
  const user = body?.user;
  const login = typeof user?.login === 'string' ? user.login.trim() : '';
  if (!login || login.length > 80 || /[\u0000-\u001f]/.test(login)) return '';
  const id = user?.id;
  const account = typeof id === 'number' ? id : typeof id === 'string' && /^[1-9]\d*$/.test(id) ? Number(id) : 0;
  return account > 0 ? String(account) : '';
}

function vintedOwnerId(item) {
  const id = item?.user?.id ?? item?.user_id;
  const account = typeof id === 'number' ? id : typeof id === 'string' && /^[1-9]\d*$/.test(id) ? Number(id) : 0;
  return account > 0 ? String(account) : '';
}

async function deleteVinted(id, deadline) {
  // Vinted's own closet delete: read the item, then POST /api/v2/items/{id}/delete
  // with the page's CSRF token. Cookies stay in Chrome.
  const headers = { Accept: 'application/json' };
  const call = async (method, path, step) => {
    try {
      const { data } = await marketplaceFetch(`https://www.vinted.com${path}`, {
        marketplace: 'vinted', step, method, credentials: 'include', deadline, headers,
      });
      return data;
    } catch (error) {
      if (method === 'GET' && error.status === 404) return null;
      throw error;
    }
  };
  const account = vintedAccountId(await call('GET', '/api/v2/users/current', 'read'));
  if (!account) throw marketplaceError('Reconnect Vinted in Anteroom first.', { code: 'signed_out' });
  const read = step => call('GET', `/api/v2/items/${id}`, step);
  const body = await read('read');
  if (!body) return { ok: true, state: 'deleted', already: true };
  const item = body.item;
  if (!item || item.id == null) throw marketplaceError('Vinted answered in a way Anteroom does not recognise.', { code: 'schema_drift' });
  if (vintedOwnerId(item) !== account) throw marketplaceError('This Vinted listing belongs to another account.');
  if (item.is_reserved === true) throw marketplaceError('This Vinted listing is reserved. It was not deleted.');
  // A sold item stays on Vinted, closed. A hidden listing is still the seller's to delete.
  if (item.is_closed === true && item.is_hidden !== true) throw marketplaceError('This Vinted listing has sold. It was not deleted.');
  const page = await marketplaceFetch('https://www.vinted.com/items/new', {
    marketplace: 'vinted', step: 'session', expect: 'text', credentials: 'include', deadline,
  });
  const csrf = vintedCsrfToken(page.data);
  if (!csrf) throw marketplaceError('Reconnect Vinted in Anteroom first.', { code: 'signed_out' });
  headers['x-csrf-token'] = csrf;
  try {
    await call('POST', `/api/v2/items/${id}/delete`, 'delete');
  } catch (error) {
    if (error.status === 404) return { ok: true, state: 'deleted', already: true };
    throw error;
  }
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (!await read('read-back')) return { ok: true, state: 'deleted' };
    if (attempt < 2) await marketplaceSleep(700);
  }
  throw marketplaceError('Vinted has not confirmed the delete. Delist again to check.', { code: 'uncertain', rejected: false });
}

async function deleteSellwild(id, deadline) {
  // Sellwild gives sellers delist, not delete; a delisted item leaves the site.
  const result = await sellwildFetch('/api/seller/listings/bulk-status', { method: 'POST', deadline, step: 'delist',
    body: { action: 'delist', listingIds: [Number(id)] } });
  if (result?.success !== true) throw marketplaceError('Sellwild did not delist the item.', { marketplace_message: marketplaceBodyMessage(result) });
  if (result.changed > 0) return { ok: true, state: 'deleted' };
  if (result.notFound > 0) throw marketplaceError('Sellwild says this listing is not on your account.');
  if (result.skipped > 0) {
    // Already delisted, or a sale is in progress: Sellwild skips both.
    throw marketplaceError('Sellwild skipped this listing: it is already delisted or has a sale in progress. Check it on Sellwild.');
  }
  throw marketplaceError('Sellwild did not confirm the delist.', { code: 'uncertain', rejected: false });
}

const MARKETPLACE_DELETE = {
  poshmark: [/^[a-f0-9]{24}$/, deletePoshmark],
  mercari: [/^m\d{6,}$/, deleteMercari],
  depop: [/^[a-z0-9][a-z0-9-]{0,200}$/, deleteDepop],
  sellwild: [/^\d{1,15}$/, deleteSellwild],
  vinted: [/^\d{1,15}$/, deleteVinted],
};

async function runMarketplaceDelete(payload) {
  const entry = MARKETPLACE_DELETE[payload?.marketplace];
  if (!entry || payload.confirm !== true || !entry[0].test(String(payload.listing_id || ''))) {
    return marketplaceFailure(marketplaceError('Confirm deleting a named marketplace listing.'), true);
  }
  try {
    return await entry[1](String(payload.listing_id), Date.now() + DELETE_DEADLINE_MS);
  } catch (error) {
    // Every delete reads the listing first, so repeating one is safe.
    return marketplaceFailure(error.code === 'uncertain' ? Object.assign(error, { code: 'network' }) : error, true);
  }
}

async function handleMarketplaceDelete(msg) {
  if (activeNativeOperation || activeJob || activePatch) {
    sendBrowserResult(msg, marketplaceFailure(marketplaceError('Another automation job is running.'), true));
    return;
  }
  activeNativeOperation = { id: `delete-${msg.payload?.request_id || ''}`, tabId: null, cancelled: false, controller: new AbortController() };
  try { sendBrowserResult(msg, await runMarketplaceDelete(msg.payload)); }
  finally { activeNativeOperation = null; }
}
