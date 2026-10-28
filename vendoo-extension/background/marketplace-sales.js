// Read recent sales from a marketplace the seller is already signed into.
// Returns listing and order IDs only. Prices, names, and messages stay in Chrome.

const POSHMARK_SALE_ID = /^[a-f0-9]{24}$/;
const DEPOP_LISTING_ID = /^\d{5,12}$/;
const DEPOP_ORDER_ID = /^\d{1,20}$/;
const MERCARI_ITEM_ID = /^m\d{8,14}$/;
const SELLWILD_ITEM_ID = /^\d{1,12}$/;

function firstMatch(node, keys, pattern) {
  if (!node || typeof node !== 'object') return null;
  for (const key of keys) {
    const value = node[key];
    if (value == null || typeof value === 'object') continue;
    const text = String(value);
    if (pattern.test(text)) return text;
  }
  return null;
}

function collectIdPairs(body, listingKeys, listingPattern, orderKeys, orderPattern) {
  const sales = [];
  const seen = new Set();
  const push = (orderId, listingId) => {
    const key = `${orderId}:${listingId}`;
    if (seen.has(key) || sales.length >= 40) return;
    seen.add(key);
    sales.push({ listing_id: listingId, order_id: orderId });
  };
  const visit = (node, parentOrder, depth) => {
    if (!node || typeof node !== 'object' || depth > 6 || sales.length >= 40) return;
    if (Array.isArray(node)) {
      node.forEach(item => visit(item, parentOrder, depth + 1));
      return;
    }
    const listingId = firstMatch(node, listingKeys, listingPattern);
    const orderId = firstMatch(node, orderKeys, orderPattern);
    const ownOrder = orderId && orderId !== listingId ? orderId : null;
    if (listingId) push(ownOrder || parentOrder || listingId, listingId);
    for (const key of ['post', 'listing', 'product']) {
      const nested = firstMatch(node[key], ['id', ...listingKeys], listingPattern);
      if (nested) push(ownOrder || parentOrder || nested, nested);
    }
    const nextOrder = ownOrder || parentOrder;
    Object.values(node).forEach(value => visit(value, nextOrder, depth + 1));
  };
  visit(body, null, 0);
  return sales;
}

async function readJson(response) {
  try { return await response.json(); } catch { return null; }
}

async function readPoshmarkSales() {
  const cookie = await chrome.cookies.get({ url: 'https://poshmark.com/', name: 'jwt' });
  const account = jwtClaims(cookieToken(cookie));
  const userId = String(account?.user_id || '');
  if (!POSHMARK_SALE_ID.test(userId)) return { ok: false, error: 'Sign in to Poshmark in Chrome first.' };
  const request = encodeURIComponent(JSON.stringify({ count: 20 }));
  let response;
  try {
    response = await fetch(`https://poshmark.com/vm-rest/users/${userId}/orders/sales?request=${request}&app_version=2.55&pm_version=2026.43.00`, {
      credentials: 'include', headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(20000),
    });
  } catch {
    return { ok: false, error: 'Poshmark did not return sales.' };
  }
  if (response.status === 401 || response.status === 403) return { ok: false, error: 'Sign in to Poshmark in Chrome again.' };
  if (!response.ok) return { ok: false, error: 'Poshmark did not return sales.' };
  const body = await readJson(response);
  if (!body) return { ok: false, error: 'Poshmark did not return sales.' };
  return {
    ok: true,
    sales: collectIdPairs(body, ['post_id', 'parent_post_id', 'listing_id'], POSHMARK_SALE_ID, ['order_id', 'id'], POSHMARK_SALE_ID),
  };
}

async function readDepopSales() {
  const cookie = await chrome.cookies.get({ url: 'https://www.depop.com/', name: 'access_token' });
  const token = cookieToken(cookie);
  if (!token) return { ok: false, error: 'Sign in to Depop in Chrome first.' };
  await ensureMarketplaceRequestContext();
  let response;
  try {
    response = await fetch('https://webapi.depop.com/api/v1/receipts/?role=seller&limit=24', {
      headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
      credentials: 'omit', signal: AbortSignal.timeout(20000),
    });
  } catch {
    return { ok: false, error: 'Depop did not return sales.' };
  }
  if (response.status === 401 || response.status === 403) return { ok: false, error: 'Sign in to Depop in Chrome first.' };
  if (!response.ok) return { ok: false, error: 'Depop did not return sales.' };
  const body = await readJson(response);
  if (!body) return { ok: false, error: 'Depop did not return sales.' };
  return {
    ok: true,
    sales: collectIdPairs(body, ['product_id', 'productId'], DEPOP_LISTING_ID, ['purchase_id', 'purchaseId', 'receipt_id'], DEPOP_ORDER_ID),
  };
}

function mercariPageSold(html, id) {
  // The status closest to this item's id. A sold item elsewhere on the page
  // must not mark this listing sold.
  const at = html.indexOf(`"${id}"`);
  if (at < 0) return null;
  const slice = html.slice(Math.max(0, at - 2000), at + id.length + 2000);
  const center = slice.indexOf(`"${id}"`);
  let sold = null;
  let distance = Infinity;
  for (const [pattern, isSold] of [
    [/"status"\s*:\s*"sold"/i, true],
    [/"itemStatus"\s*:\s*"ITEM_STATUS_SOLD"/, true],
    [/"status"\s*:\s*"on_sale"/i, false],
    [/"itemStatus"\s*:\s*"ITEM_STATUS_ON_SALE"/, false],
  ]) {
    const match = pattern.exec(slice);
    if (!match || Math.abs(match.index - center) >= distance) continue;
    distance = Math.abs(match.index - center);
    sold = isSold;
  }
  return sold;
}

// Mercari answers a scripted page load with a verification challenge rather
// than the listing, so a sold item never appears in what comes back. Name that
// instead of asking the seller to sign in again, which cannot fix it.
function mercariChallengePage(html) {
  return /Just a moment|__cf_chl|cf-browser-verification|challenge-platform|verify you are human/i.test(html || '');
}

async function readMercariSales(listingIds) {
  const sales = [];
  let checked = 0;
  let failed = 0;
  let challenged = 0;
  for (const id of listingIds) {
    if (!MERCARI_ITEM_ID.test(id) || sales.length >= 40) continue;
    checked += 1;
    let response;
    try {
      response = await fetch(`https://www.mercari.com/us/item/${id}/`, {
        credentials: 'include', headers: { Accept: 'text/html' }, signal: AbortSignal.timeout(15000),
      });
    } catch { failed += 1; continue; }
    let html = '';
    try { html = (await response.text()).slice(0, 300000); } catch { html = ''; }
    if (mercariChallengePage(html)) { challenged += 1; failed += 1; continue; }
    if (!response.ok) { failed += 1; continue; }
    if (mercariPageSold(html, id) === true) sales.push({ listing_id: id, order_id: id });
  }
  if (checked && challenged === checked) {
    return { ok: false, error: 'Mercari is asking for browser verification, so its listings cannot be read. Sales there are not detected until that stops.' };
  }
  if (checked && failed === checked) return { ok: false, error: 'Could not check sales.' };
  return { ok: true, sales };
}

function sellwildSold(body) {
  const listing = body?.listing && typeof body.listing === 'object' ? body.listing : body;
  if (!listing || typeof listing !== 'object') return null;
  if (listing.sold === true) return true;
  const status = String(listing.status || '').toLowerCase();
  if (status === 'sold' || status === 'sold_out') return true;
  if (status === 'active' || status === 'published' || status === 'live') return false;
  return null;
}

async function readSellwildSales(listingIds) {
  const sales = [];
  let checked = 0;
  let failed = 0;
  for (const id of listingIds) {
    if (!SELLWILD_ITEM_ID.test(id) || sales.length >= 40) continue;
    checked += 1;
    let response;
    try {
      response = await fetch(`https://sellwild.com/api/listings/${id}`, {
        credentials: 'include', headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(15000),
      });
    } catch { failed += 1; continue; }
    if (!response.ok) { failed += 1; continue; }
    const body = await readJson(response);
    if (!body) { failed += 1; continue; }
    if (sellwildSold(body) === true) sales.push({ listing_id: id, order_id: id });
  }
  if (checked && failed === checked) return { ok: false, error: 'Could not check sales.' };
  return { ok: true, sales };
}

async function readMarketplaceSales(marketplace, listingIds) {
  if (activeNativeOperation) return { ok: true, sales: [], skipped: true };
  const ids = (Array.isArray(listingIds) ? listingIds : []).filter(id => typeof id === 'string').slice(0, 15);
  if (marketplace === 'poshmark') return readPoshmarkSales();
  if (marketplace === 'depop') return readDepopSales();
  if (marketplace === 'mercari') return readMercariSales(ids);
  if (marketplace === 'sellwild') return readSellwildSales(ids);
  return { ok: false, error: 'Could not check sales.' };
}

async function handleMarketplaceSales(msg) {
  try {
    const payload = msg.payload || {};
    sendBrowserResult(msg, await readMarketplaceSales(payload.marketplace, payload.listing_ids));
  } catch {
    sendBrowserResult(msg, { ok: false, error: 'Could not check sales.' });
  }
}
