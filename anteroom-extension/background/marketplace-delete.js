// Seller-confirmed delisting from the extension worker, with no marketplace tab.
// Each step reads the listing first: one already gone counts as deleted, so a
// delete whose reply was lost is safe to repeat. Requests come from each
// marketplace's own web client (read 2026-10-24).
const DELETE_DEADLINE_MS = 60000;
const MERCARI_GONE = /^Item is no longer available\.?$/i;

async function deletePoshmark(id, deadline, region) {
  const origin = poshmarkOrigin(region);
  const account = await poshmarkAccount(origin);
  const client = poshmarkClient(deadline, await poshmarkCsrf(deadline, origin), origin);
  // Poshmark answers errors with HTTP 200 and an error body.
  const gone = error => ['PostRemovedError', 'NotFoundError'].includes(error?.marketplace_type);
  const read = async step => {
    try { return { post: await client.call('GET', `/vm-rest/posts/${id}?app_type=web`, undefined, { step, what: 'the read' }) }; }
    catch (error) {
      if (gone(error)) return { post: null, message: error.marketplace_message };
      throw error;
    }
  };
  const { post, message } = await read('read');
  if (!post) return { ok: true, state: 'deleted', already: true, marketplace_message: message };
  if (String(post.creator_id) !== account.user_id) throw marketplaceError('This Poshmark listing belongs to another account.');
  const sold = (post.inventory?.size_quantities || []).some(entry => Number(entry.quantity_sold) > 0);
  if (sold || post.status === 'archived' || ['sold_out', 'not_available'].includes(post.inventory?.status)) {
    throw marketplaceError('This Poshmark listing has sold. It was not deleted.');
  }
  await client.call('DELETE', `/vm-rest/posts/${id}?app_type=web`, undefined, { step: 'delete', what: 'the delete' });
  if ((await read('read-back')).post) throw marketplaceError('Poshmark has not confirmed the delete. Delist again to check.', { code: 'uncertain', rejected: false });
  return { ok: true, state: 'deleted' };
}

async function deleteMercari(id, deadline, verifyReplacement) {
  mercariSession = null;
  const session = await mercariCurrentSession(deadline);
  let item;
  try { item = await readMercariItem(id, deadline); }
  catch (error) {
    // Mercari answers the read of a deleted item with this refusal (2026-11-20).
    if (error.code === 'rejected' && MERCARI_GONE.test(error.marketplace_message || '')) return { ok: true, state: 'deleted', already: true, marketplace_message: error.marketplace_message };
    throw error;
  }
  // cancel / admin_cancel is the item status once it is gone.
  if (['cancel', 'admin_cancel'].includes(item.status)) return { ok: true, state: 'deleted', already: true };
  if (String(item.seller?.id ?? '') !== String(session.user.userId)) {
    throw marketplaceError('This Mercari listing belongs to another account. It was not deleted.');
  }
  if (item.status !== 'on_sale') {
    throw marketplaceError(`This Mercari listing is not on sale${item.status ? ` (${item.status})` : ''}. It was not deleted.`);
  }
  if (verifyReplacement) await verifyReplacement();
  // Mercari's own Delete button: updateItemStatus to "cancel". Its reply
  // carries the new status; an item already removed reports the same.
  const data = await mercariRequest({ operationName: 'UpdateItemStatusMutation', variables: { input: { id, status: 'cancel' } },
    extensions: { persistedQuery: { version: 1, sha256Hash: '55bd4e7d2bc2936638e1451da3231e484993635d7603431d1a2978e3d59656f8' } } },
  { deadline, operationName: 'UpdateItemStatusMutation', resend: !verifyReplacement });
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


const MARKETPLACE_DELETE = {
  poshmark: [/^[a-f0-9]{24}$/, deletePoshmark],
  mercari: [/^m\d{6,}$/, deleteMercari],
  depop: [/^[a-z0-9][a-z0-9-]{0,200}$/, deleteDepop],
};

async function runMarketplaceDelete(payload) {
  const entry = MARKETPLACE_DELETE[payload?.marketplace];
  if (!entry || payload.confirm !== true || !entry[0].test(String(payload.listing_id || ''))) {
    return marketplaceFailure(marketplaceError('Confirm deleting a named marketplace listing.'), true);
  }
  try {
    const deadline = Date.now() + DELETE_DEADLINE_MS;
    if (payload.marketplace === 'depop' && payload.action === 'relist') {
      // An accepted live-listing read says nothing about access to the drafts
      // API needed to recreate it. Check drafts before touching the live item.
      await depopAction('session', { deadline: Math.min(deadline, Date.now() + 10000) });
    }
    return await entry[1](String(payload.listing_id), deadline, payload.marketplace === 'poshmark' ? payload.seller_region : undefined);
  } catch (error) {
    // Every delete reads the listing first, so repeating one is safe.
    return marketplaceFailure(error.code === 'uncertain' ? Object.assign(error, { code: 'network' }) : error, true);
  }
}

async function handleMarketplaceDelete(msg, reply = result => sendBrowserResult(msg, result)) {
  const operation = claimNativeOperation('*', `delete-${msg.payload?.request_id || ''}`);
  if (!operation) {
    reply(marketplaceFailure(marketplaceError('Another automation job is running.'), true));
    return;
  }
  try { reply(await runMarketplaceDelete(msg.payload)); }
  finally { releaseNativeOperation(operation); }
}
