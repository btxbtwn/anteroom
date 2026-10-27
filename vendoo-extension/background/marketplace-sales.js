// Find which of the seller's listings have sold. Returns listing IDs only.
// Prices, names, and messages stay in Chrome.
//
// Each check costs a few requests however many listings the seller has. The
// seller's account comes from one of their own listings, so no sign-in is
// needed. Recorded against live and sold listings on 2026-10-25:
//   Depop     shop sold list, newest sale first; status "STATUS_PURCHASED" or "STATUS_MARKED_AS_SOLD".
//   Mercari   the seller's items in "trading" (sold, not yet completed). Every
//             sale passes through it for days. The item page carries the
//             access token and seller this search needs.
//   Poshmark  the seller's live closet; a known listing missing from it is read
//             on its own, sold when data.inventory.status is "sold_out".
//   Sellwild  the seller's live listings; a known listing missing from them is
//             read on its own, sold when listing.status is 5 (1 while live).
//   Vinted    the seller's wardrobe; a known listing missing from the live rows
//             is read on its own, sold when it is closed and not hidden or reserved.
//   Facebook  each listing's own item page, a few per pass several seconds apart,
//             sold when the listing record says is_sold (facebook-native.js).

const SALE_LISTING_ID = {
  poshmark: /^[a-f0-9]{24}$/,
  mercari: /^m\d{6,}$/,
  depop: /^[a-z0-9][a-z0-9-]{0,200}$/,
  sellwild: /^\d{1,15}$/,
  vinted: /^\d{1,15}$/,
  facebook: FACEBOOK_LISTING_ID,
};
const SALE_MAX_LISTINGS = 5000;
const SALE_MAX_PAGES = 40;
const SALE_MAX_CONFIRM = 40;
const MERCARI_PROFILE_SEARCH = 'ddf46d28377df85dd43bbd8a9750c0eb1c0a95e669d6509ea1b15af6d0fd507f';

// A short wait between requests. Tests replace setTimeout with a stub that
// returns 0 and never fires, so the wait is skipped there.
async function salePause(betweenPages) {
  const ms = betweenPages
    ? 800 + Math.floor(Math.random() * 1700)
    : 300 + Math.floor(Math.random() * 600);
  await new Promise(resolve => {
    const timer = setTimeout(resolve, ms);
    if (timer === 0) resolve();
  });
}

async function saleRead(url, options = {}) {
  const response = await fetch(url, { signal: AbortSignal.timeout(15000), ...options });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response;
}

async function saleJson(url, options = {}) {
  const response = await saleRead(url, { ...options, headers: { Accept: 'application/json', ...options.headers } });
  return response ? response.json() : null;
}

// The first of the seller's listings that names its account.
async function sellerFrom(ids, read) {
  for (const id of ids.slice(0, 5)) {
    const seller = await read(id).catch(() => null);
    if (seller) return seller;
  }
  throw new Error('Seller not found');
}

// Known listings missing from the live list, each confirmed on its own.
async function soldAmongMissing(ids, live, isSold) {
  const sales = [];
  for (const id of ids.filter(id => !live.has(id)).slice(0, SALE_MAX_CONFIRM)) {
    await salePause(false);
    if (await isSold(id).catch(() => false)) sales.push({ listing_id: id });
  }
  return sales;
}

async function depopJson(path) {
  await ensureMarketplaceRequestContext();
  return saleJson(`https://webapi.depop.com/presentation/api/v1/${path}`, { credentials: 'omit' });
}

async function depopSales(slugs) {
  const shop = await sellerFrom(slugs, async slug => {
    const id = String((await depopJson(`products/by-slug/${slug}/`))?.user_id ?? '');
    return /^\d{1,15}$/.test(id) ? id : null;
  });
  await salePause(true);
  const sold = await depopJson(`shops/${shop}/products/by-status/sold/?limit=24`);
  if (!Array.isArray(sold?.objects)) throw new Error('Depop sold items missing');
  return sold.objects
    .filter(product => ['STATUS_PURCHASED', 'STATUS_MARKED_AS_SOLD'].includes(product?.status))
    .map(product => String(product?.slug ?? ''))
    .filter(slug => SALE_LISTING_ID.depop.test(slug))
    .map(slug => ({ listing_id: slug }));
}

function mercariPageData(html) {
  const match = /<script id="__NEXT_DATA__" type="application\/json"[^>]*>([\s\S]*?)<\/script>/.exec(html);
  return match ? JSON.parse(match[1])?.props?.pageProps : null;
}

async function mercariSales(ids) {
  const { token, seller } = await sellerFrom(ids, async id => {
    // Mercari answers HTTP 500 when the request asks for text/html explicitly.
    const response = await saleRead(`https://www.mercari.com/us/item/${id}/`, { credentials: 'include' });
    const page = response ? mercariPageData(await response.text()) : null;
    const ref = String(page?.serverState?.[`ItemDetail:${id}`]?.seller?.__ref ?? '');
    const sellerId = /^PublicUser:(\d{1,15})$/.exec(ref)?.[1];
    return sellerId && page?.req?.accessToken ? { token: page.req.accessToken, seller: Number(sellerId) } : null;
  });
  await salePause(true);
  await ensureMarketplaceRequestContext();
  const response = await saleRead('https://www.mercari.com/v1/api', {
    method: 'POST', credentials: 'include',
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json', 'x-platform': 'web',
      'x-double-web': '1', 'apollo-require-preflight': 'true' },
    body: JSON.stringify({ operationName: 'publicProfileSearchQuery',
      variables: { criteria: { itemStatuses: [2], sellerIds: [seller], length: 200 }, userId: seller },
      extensions: { persistedQuery: { version: 1, sha256Hash: MERCARI_PROFILE_SEARCH } } }),
  });
  const items = response ? (await response.json())?.data?.search?.itemsList : null;
  if (!Array.isArray(items)) throw new Error('Mercari items missing');
  return items
    .filter(item => item?.status === 'trading' && SALE_LISTING_ID.mercari.test(String(item.id)))
    .map(item => ({ listing_id: item.id }));
}

async function poshmarkPost(id) {
  return (await saleJson(`https://poshmark.com/vm-rest/posts/${id}?app_version=2.55`, { credentials: 'include' }))?.data;
}

async function poshmarkSales(ids) {
  const seller = await sellerFrom(ids, async id => {
    const creator = String((await poshmarkPost(id))?.creator_id ?? '');
    return SALE_LISTING_ID.poshmark.test(creator) ? creator : null;
  });
  const pending = new Set(ids);
  const live = new Set();
  let maxId = null;
  for (let page = 0; page < SALE_MAX_PAGES && pending.size; page += 1) {
    if (page > 0) await salePause(true);
    const request = { filters: { department: 'All', inventory_status: ['available'] }, sort_by: 'added_desc',
      experience: 'all', count: 200, ...(maxId ? { max_id: maxId } : {}) };
    const body = await saleJson(`https://poshmark.com/vm-rest/users/${seller}/posts/filtered?request=${encodeURIComponent(JSON.stringify(request))}&summarize=true&app_version=2.55`, { credentials: 'include' });
    if (!Array.isArray(body?.data)) throw new Error('Poshmark closet missing');
    body.data.forEach(post => {
      const id = String(post?.id ?? '');
      live.add(id);
      pending.delete(id);
    });
    maxId = body.more?.next_max_id || null;
    if (!maxId) break;
  }
  // A page still waiting means the closet was not finished, so a missing id
  // might be further down rather than sold.
  if (pending.size && maxId) throw new Error('Poshmark closet too large to read');
  return soldAmongMissing(ids, live, async id => (await poshmarkPost(id))?.inventory?.status === 'sold_out');
}

async function sellwildListing(id) {
  await ensureMarketplaceRequestContext();
  return (await saleJson(`https://sellwild.com/api/listings/${id}`, { credentials: 'include' }))?.listing;
}

async function sellwildSales(ids) {
  const seller = await sellerFrom(ids, async id => {
    const user = String((await sellwildListing(id))?.user?.id ?? '');
    return /^\d{1,15}$/.test(user) ? user : null;
  });
  const pending = new Set(ids);
  const live = new Set();
  let more = true;
  for (let page = 1; more && pending.size && page <= SALE_MAX_PAGES; page += 1) {
    if (page > 1) await salePause(true);
    await ensureMarketplaceRequestContext();
    const body = await saleJson(`https://sellwild.com/api/search/listings?userId=${seller}&limit=200&page=${page}`, { credentials: 'include' });
    if (body?.success !== true || !Array.isArray(body.listings)) throw new Error('Sellwild listings missing');
    body.listings.forEach(listing => {
      const id = String(listing?.id ?? '');
      live.add(id);
      pending.delete(id);
    });
    more = body.hasMore === true;
  }
  if (pending.size && more) throw new Error('Sellwild shop too large to read');
  return soldAmongMissing(ids, live, async id => (await sellwildListing(id))?.status === 5);
}

async function vintedSales(ids) {
  await ensureMarketplaceRequestContext();
  const account = vintedAccountId(await saleJson('https://www.vinted.com/api/v2/users/current', { credentials: 'include' }));
  if (!account) throw new Error('Seller not found');
  const pending = new Set(ids);
  const live = new Set();
  let page = 1;
  let total = 1;
  let finished = false;
  while (page <= total && page <= SALE_MAX_PAGES && pending.size) {
    if (page > 1) await salePause(true);
    const body = await saleJson(`https://www.vinted.com/api/v2/wardrobe/${account}/items?page=${page}&per_page=96&order=newest_first`, { credentials: 'include' });
    if (!Array.isArray(body?.items)) throw new Error('Vinted wardrobe missing');
    total = Number(body.pagination?.total_pages) || page;
    body.items.forEach(item => {
      const id = String(item?.id ?? '');
      if (!SALE_LISTING_ID.vinted.test(id)) return;
      if (item.is_draft !== true && item.is_closed !== true && item.is_hidden !== true) {
        live.add(id);
        pending.delete(id);
      }
    });
    if (page >= total || !body.items.length) { finished = true; break; }
    page += 1;
  }
  if (pending.size && !finished) throw new Error('Vinted wardrobe too large to read');
  return soldAmongMissing(ids, live, async id => {
    const item = (await saleJson(`https://www.vinted.com/api/v2/items/${id}`, { credentials: 'include' }))?.item;
    return Boolean(item && item.is_closed === true && item.is_hidden !== true && item.is_reserved !== true && item.is_draft !== true);
  });
}

const SALE_READERS = { depop: depopSales, mercari: mercariSales, poshmark: poshmarkSales, sellwild: sellwildSales, vinted: vintedSales,
  facebook: facebookSales };

async function readMarketplaceSales(marketplace, listingIds) {
  if (activeNativeOperation) return { ok: true, sales: [], skipped: true };
  const read = SALE_READERS[marketplace];
  if (!read) return { ok: false, error: 'Could not check sales.' };
  const ids = (Array.isArray(listingIds) ? listingIds : [])
    .filter(id => typeof id === 'string' && SALE_LISTING_ID[marketplace].test(id))
    .slice(0, SALE_MAX_LISTINGS);
  if (!ids.length) return { ok: true, sales: [] };
  try {
    return { ok: true, sales: await read(ids) };
  } catch {
    return { ok: false, error: 'Could not check sales.' };
  }
}

async function handleMarketplaceSales(msg) {
  try {
    const payload = msg.payload || {};
    sendBrowserResult(msg, await readMarketplaceSales(payload.marketplace, payload.listing_ids));
  } catch {
    sendBrowserResult(msg, { ok: false, error: 'Could not check sales.' });
  }
}
