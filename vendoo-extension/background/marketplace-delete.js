// Seller-confirmed delisting from the extension worker, with no marketplace tab.
// Each step reads the listing first: one already gone counts as deleted, so a
// delete whose reply was lost is safe to repeat. Requests come from each
// marketplace's own web client (read 2026-10-24).
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
  const status = String(data.updateItemStatus?.status || '');
  if (!['cancel', 'admin_cancel'].includes(status)) {
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
  const after = await call('GET', `/presentation/api/v1/products/by-slug/${encodeURIComponent(slug)}/`, 'read-back');
  if (after && !String(after.status || '').toUpperCase().includes('DELETED')) {
    throw marketplaceError('Depop has not confirmed the delete. Delist again to check.', { code: 'uncertain', rejected: false });
  }
  return { ok: true, state: 'deleted' };
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
