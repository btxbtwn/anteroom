// Seller-confirmed shares and offers on live listings, from the extension worker.
// Requests come from Poshmark's own web client (read 2026-10-26):
// - Share to followers: PUT /vm-rest/users/self/shared_posts/{post}
//   (handled refusals SharePostRequestLimitExceededError, ValidationError and
//   a captcha for the share_post action).
// - Offer to likers: POST /vm-rest/posts/{post}/likes/users/offers with
//   { offer_amount: { val, currency_code }, seller_shipping_discount: { id },
//   offer_api_version: 3 }. The offer is at most
//   floor(price × (100 − minimum_seller_offer_discount_percent) / 100) from the
//   offer_to_likers__us feature setting, and the shipping discounts come from
//   /vm-rest/users/{id}/seller_shipping_discounts/offer.
// Every mutation is journaled under its action ID before it is sent. A journal
// that exists is answered from, never sent again.
const PROMOTION_DEADLINE_MS = 60000;
const POSHMARK_SHARE_GAP_MS = 4000;
const POSHMARK_SHARE_GAP_MAX_MS = 30000;
const POSHMARK_SHARE_JITTER_MS = 3000;
const POSHMARK_LAST_SHARE_KEY = 'poshmark_last_share_at';

function promotionKey(actionId) {
  return `promotion_${actionId}`;
}

function poshmarkCents(amount) {
  const value = Number(amount?.val ?? amount);
  return Number.isFinite(value) && value > 0 ? Math.round(value * 100) : 0;
}

async function poshmarkOwnPost(client, account, id) {
  const post = await client.call('GET', `/vm-rest/posts/${id}?app_type=web`, undefined, { step: 'read', what: 'the read' });
  if (String(post.creator_id) !== account.user_id) throw marketplaceError('This Poshmark listing belongs to another account.');
  const sold = (post.inventory?.size_quantities || []).some(entry => Number(entry.quantity_sold) > 0);
  if (sold || post.status === 'archived' || ['sold_out', 'not_available'].includes(post.inventory?.status)) {
    throw marketplaceError('This Poshmark listing has sold or is not available.');
  }
  if (post.status && post.status !== 'published') throw marketplaceError('This Poshmark listing is not live.');
  return post;
}

// The strictest offer-to-likers rule across Poshmark's US variants, so the
// seller's own segment never allows less than Anteroom assumes.
async function poshmarkOfferRules(deadline) {
  const { data } = await marketplaceFetch(`${POSHMARK}/vm-rest/meta/feature_settings?client_app=web_feature_settings`, {
    marketplace: 'poshmark', step: 'offer-rules', credentials: 'omit', headers: { Accept: 'application/json' }, deadline, timeoutMs: 20000 });
  const variants = Object.values((data?.data || data)?.offer_to_likers__us || {}).filter(row => row && typeof row === 'object');
  const enabled = variants.filter(row => row.enabled === true);
  const minimum = Math.max(...enabled.map(row => Number(row.minimum_seller_offer_discount_percent)));
  const maximum = Math.min(...enabled.map(row => Number(row.maximum_seller_offer_discount_percent)));
  if (!enabled.length || !(minimum > 0 && minimum < 100) || !(maximum > minimum && maximum < 100)) {
    throw marketplaceError('Poshmark’s offer-to-likers rules changed. Anteroom needs an update before sending offers.', { code: 'schema_drift' });
  }
  return { minimum, maximum };
}

// Whole dollars, as Poshmark's own offer form computes them.
function poshmarkOfferCents(priceCents, percent) {
  return Math.floor((priceCents / 100) * (100 - percent) / 100) * 100;
}

async function poshmarkOfferShipping(client, account, id, offerCents) {
  const amount = encodeURIComponent(JSON.stringify({ val: String(offerCents / 100), currency_code: 'USD' }));
  const rows = await client.call('GET', `/vm-rest/users/${account.user_id}/seller_shipping_discounts/offer?price_amount=${amount}&object_id=${id}&app_version=5.04`,
    undefined, { step: 'offer-shipping', what: 'shipping discounts' });
  const list = Array.isArray(rows) ? rows : Array.isArray(rows?.discounts) ? rows.discounts : null;
  if (!list || list.some(row => !/^[a-f0-9]{24}$/.test(String(row.id)) || typeof row.label !== 'string')) {
    throw marketplaceError('Poshmark’s shipping discounts changed. Anteroom needs an update before sending offers.', { code: 'schema_drift' });
  }
  return list.filter(row => row.enabled !== false).map(row => ({ id: String(row.id), label: marketplaceScrub(row.label).slice(0, 80),
    min_offer_cents: poshmarkCents(row.min_offer_amount) || null }));
}

// Read-only: what a share or an offer would do on this listing now.
async function checkPoshmarkPromotion(payload, deadline) {
  const account = await poshmarkAccount();
  const client = poshmarkClient(deadline, '');
  const post = await poshmarkOwnPost(client, account, payload.listing_id);
  const price = poshmarkCents(post.price_amount);
  if (!price) throw marketplaceError('Poshmark did not return this listing’s price.', { code: 'schema_drift' });
  const facts = { ok: true, price_cents: price, like_count: Number(post.like_count) || 0 };
  if (payload.action === 'share') return facts;
  const rules = await poshmarkOfferRules(deadline);
  const percent = Number(payload.discount_percent);
  const limits = { minimum_discount_percent: rules.minimum, maximum_discount_percent: rules.maximum };
  if (!Number.isInteger(percent) || percent < rules.minimum || percent > rules.maximum) {
    return { ...facts, ...limits, offer_price_cents: null, shipping_discounts: [] };
  }
  const offer = poshmarkOfferCents(price, percent);
  const shipping = offer > 0 ? await poshmarkOfferShipping(client, account, payload.listing_id, offer) : [];
  return { ...facts, ...limits, offer_price_cents: offer, shipping_discounts: shipping };
}

async function sharePoshmark(payload, deadline) {
  const account = await poshmarkAccount();
  const client = poshmarkClient(deadline, await poshmarkCsrf(deadline));
  await poshmarkOwnPost(client, account, payload.listing_id);
  // Space shares the way a person would, across batches and restarts. A
  // scheduled round may ask for a slower pace, never a faster one.
  const gap = Math.min(POSHMARK_SHARE_GAP_MAX_MS, Math.max(POSHMARK_SHARE_GAP_MS, Number(payload.gap_ms) || 0));
  const last = Number((await chrome.storage.local.get(POSHMARK_LAST_SHARE_KEY))[POSHMARK_LAST_SHARE_KEY]) || 0;
  const wait = last + gap + Math.floor(Math.random() * POSHMARK_SHARE_JITTER_MS) - Date.now();
  if (wait > 0) await marketplaceSleep(Math.min(wait, gap + POSHMARK_SHARE_JITTER_MS));
  await chrome.storage.local.set({ [POSHMARK_LAST_SHARE_KEY]: Date.now() });
  await client.call('PUT', `/vm-rest/users/self/shared_posts/${payload.listing_id}`, {}, { step: 'share', what: 'the share' });
  return { ok: true, state: 'done' };
}

async function offerPoshmark(payload, deadline) {
  const offer = Number(payload.offer_price_cents);
  const floor = Number(payload.floor_cents);
  if (!Number.isInteger(offer) || offer <= 0 || offer % 100 || !Number.isInteger(floor) || floor <= 0 || offer < floor) {
    throw marketplaceError('This offer is below your floor or not whole dollars. Review it again.');
  }
  const account = await poshmarkAccount();
  const client = poshmarkClient(deadline, await poshmarkCsrf(deadline));
  const post = await poshmarkOwnPost(client, account, payload.listing_id);
  if (poshmarkCents(post.price_amount) !== Number(payload.price_cents)) {
    throw marketplaceError('The Poshmark price changed since your review. Review the offer again.');
  }
  if (!(Number(post.like_count) > 0)) throw marketplaceError('This Poshmark listing has no likers to send an offer to.');
  const rules = await poshmarkOfferRules(deadline);
  if (offer > poshmarkOfferCents(Number(payload.price_cents), rules.minimum)) {
    throw marketplaceError(`Poshmark needs offers to likers at least ${rules.minimum}% below the price. Review the offer again.`);
  }
  const body = { offer_amount: { val: String(offer / 100), currency_code: 'USD' }, offer_api_version: 3 };
  if (payload.shipping_discount_id) {
    if (!/^[a-f0-9]{24}$/.test(String(payload.shipping_discount_id))) throw marketplaceError('Choose the shipping discount again.');
    body.seller_shipping_discount = { id: String(payload.shipping_discount_id) };
  }
  await client.call('POST', `/vm-rest/posts/${payload.listing_id}/likes/users/offers`, body, { step: 'offer', what: 'the offer to likers' });
  return { ok: true, state: 'done', recipient_count: Number(post.like_count) || 0 };
}

// Mercari's and Depop's offers to likers. Their rules come from their help
// pages; the requests come from one capture of the seller's own signed-in
// browser (docs/vendoo-feature-parity.md, gap 4). A null entry has not been
// captured yet, so checks and offers refuse before any request is made:
// - likers: the read behind the offer screen, giving the like count (and, on
//   Mercari, the lowest price the item has had).
// - offer: the request the "Offer to likers" / "Send offer" button sends.
// Mercari (help article 347): at least 10% below the item's lowest price so
// far, whole dollars, at least $1, to up to 50 recent likers, for 24 hours.
// Depop (Send Offer help): to likers in the seller's country, one active offer
// per buyer, not on listings with several sizes, shipping not included. Its
// partner Selling API requires at least 5% off; the web client is assumed to
// be no looser. Depop's Selling Hub (read 2026-10-26):
// - likers: GET /presentation/api/v1/offers/me/products/ lists the seller's
//   items that have likers to offer to, with offer_count. Only
//   page_info.has_more was seen, never the next-page parameter, so an item
//   past the first page is refused rather than guessed at.
// - preview: GET /presentation/api/v1/offers/products/{id}/offers/send-all/
//   names one offer_recipient_id plus offer_additional_recipients_count
//   others, and sellers_lowest_offer_value, the lowest offer Depop allows.
// - offer: POST /presentation/api/v1/products/{id}/offers/ with
//   { offer_recipient_id, offer_currency, offer_value }, answered 200 with no
//   body. "Send offer to all likers" sent exactly one such POST, with the
//   preview's recipient. Anteroom sends that same single request, never one
//   per liker, and records the preview's reach as the expected recipients.
const PENDING_OFFER_REQUESTS = {
  // likers: { operationName, sha256Hash, variables: id => ({}), read: data => ({ like_count, lowest_price_cents }) }
  // offer:  { operationName, sha256Hash, variables: (id, offerCents) => ({}), confirmed: data => boolean }
  mercari: { likers: null, offer: null },
  depop: {
    likers: { path: () => '/presentation/api/v1/offers/me/products/', read: depopOfferLikers },
    preview: { path: product => `/presentation/api/v1/offers/products/${product.id}/offers/send-all/`, read: depopOfferPreview },
    offer: { method: 'POST', path: product => `/presentation/api/v1/products/${product.id}/offers/`,
      body: (preview, offerCents) => ({ offer_recipient_id: preview.recipient_id, offer_currency: 'USD', offer_value: (offerCents / 100).toFixed(2) }),
      confirmed: ({ status, data }) => status >= 200 && status < 300 && !/<html|<!doctype/i.test(String(data || '')) },
  },
};
const MERCARI_OFFER_MIN_PERCENT = 10;
const MERCARI_OFFER_MAX_LIKERS = 50;
const DEPOP_OFFER_MIN_PERCENT = 5;

function pendingOfferRequests(marketplace) {
  const requests = PENDING_OFFER_REQUESTS[marketplace];
  if (!requests?.likers || !requests?.offer) {
    throw marketplaceError(`Anteroom cannot send ${marketplaceLabel(marketplace)} offers yet: its offer request still needs to be read from the signed-in site.`,
      { code: 'not_captured' });
  }
  return requests;
}

// At or below the discounted price: whole dollars on Mercari, cents on Depop.
function wholeDollarOffer(priceCents, percent) {
  return Math.floor(priceCents * (100 - percent) / 10000) * 100;
}

function centOffer(priceCents, percent) {
  return Math.floor(priceCents * (100 - percent) / 100);
}

// `lowest`, when the marketplace names one, is the smallest offer it allows.
function offerFacts(state, percent, round) {
  const minimum = Math.max(1, Math.ceil(100 - state.highest * 100 / state.price));
  const maximum = state.lowest ? Math.min(99, Math.floor(100 - state.lowest * 100 / state.price)) : 99;
  const offer = Number.isInteger(percent) ? round(state.price, percent) : 0;
  const allowed = percent >= minimum && percent <= maximum && offer >= Math.max(100, state.lowest || 0) && offer <= state.highest;
  return { ok: true, price_cents: state.price, like_count: state.likes, minimum_discount_percent: minimum, maximum_discount_percent: maximum,
    shipping_discounts: [], offer_price_cents: allowed ? offer : null };
}

function checkedOffer(payload, wholeDollars) {
  const offer = Number(payload.offer_price_cents);
  const floor = Number(payload.floor_cents);
  if (!Number.isInteger(offer) || offer < 100 || (wholeDollars && offer % 100) || !Number.isInteger(floor) || floor <= 0 || offer < floor) {
    throw marketplaceError('This offer is below your floor or not a price the marketplace accepts. Review it again.');
  }
  return offer;
}

async function mercariOfferState(requests, id, deadline) {
  const session = await mercariCurrentSession(deadline, true);
  const data = await mercariRequest({ operationName: 'sellFetchItemsDetails', variables: { itemId: id }, query: MERCARI_ITEM_DETAILS_QUERY },
    { deadline, retry: true });
  const item = data?.item;
  if (String(item?.itemId ?? '') !== id) throw marketplaceError('Mercari did not return this listing.', { code: 'schema_drift' });
  if (String(item.seller?.id) !== String(session.user.userId)) throw marketplaceError('This Mercari listing belongs to another account.');
  if (item.status !== 'on_sale') throw marketplaceError('This Mercari listing has sold or is not on sale.');
  const price = Number(item.price);
  const likers = requests.likers.read(await mercariRequest({ operationName: requests.likers.operationName,
    variables: requests.likers.variables(id), extensions: { persistedQuery: { version: 1, sha256Hash: requests.likers.sha256Hash } } },
  { deadline, retry: true }));
  const lowest = Number(likers?.lowest_price_cents) || price;
  if (!(price > 0) || !(lowest > 0)) throw marketplaceError('Mercari did not return this listing’s price.', { code: 'schema_drift' });
  return { price, likes: Number(likers?.like_count) || 0, highest: wholeDollarOffer(Math.min(price, lowest), MERCARI_OFFER_MIN_PERCENT) };
}

async function checkMercariPromotion(payload, deadline) {
  const requests = pendingOfferRequests('mercari');
  const state = await mercariOfferState(requests, payload.listing_id, deadline);
  return offerFacts(state, Number(payload.discount_percent), wholeDollarOffer);
}

async function offerMercari(payload, deadline) {
  const offer = checkedOffer(payload, true);
  const requests = pendingOfferRequests('mercari');
  const state = await mercariOfferState(requests, payload.listing_id, deadline);
  if (state.price !== Number(payload.price_cents)) throw marketplaceError('The Mercari price changed since your review. Review the offer again.');
  if (!(state.likes > 0)) throw marketplaceError('This Mercari listing has no likers to send an offer to.');
  if (offer > state.highest) throw marketplaceError('Mercari needs offers to likers at least 10% below the lowest price this item has had. Review the offer again.');
  const data = await mercariRequest({ operationName: requests.offer.operationName, variables: requests.offer.variables(payload.listing_id, offer),
    extensions: { persistedQuery: { version: 1, sha256Hash: requests.offer.sha256Hash } } }, { deadline });
  if (!requests.offer.confirmed(data)) {
    throw marketplaceError('Mercari did not confirm the offer.', { code: 'uncertain', rejected: false });
  }
  return { ok: true, state: 'done', recipient_count: Math.min(state.likes, MERCARI_OFFER_MAX_LIKERS) };
}

// The offer POST answers 200 with no body, so it is read as text.
async function depopOfferCall(method, path, deadline, step, { body, expect } = {}) {
  return await marketplaceFetch(`${DEPOP_WEBAPI}${path}`, { marketplace: 'depop', step, method, credentials: 'omit', deadline,
    headers: { Accept: 'application/json', Authorization: await depopBearer(), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }), ...(expect ? { expect } : {}) });
}

// Depop's money strings, such as "42.50".
function depopOfferCents(value) {
  const text = String(value ?? '').trim();
  return /^\d{1,7}(\.\d{1,2})?$/.test(text) ? Math.round(Number(text) * 100) : null;
}

function depopOfferDrift(what) {
  return marketplaceError(`Depop’s ${what} changed. Anteroom needs an update before sending offers.`, { code: 'schema_drift' });
}

// The item's row in the Selling Hub offers list. No row means no liker an offer can reach.
function depopOfferLikers(body, product) {
  if (!Array.isArray(body?.objects)) throw depopOfferDrift('offers list');
  const row = body.objects.find(entry => String(entry?.product_id) === String(product.id));
  if (!row) {
    if (body.page_info?.has_more === true) {
      throw marketplaceError('Depop lists this item past the first page of its offers. Send this offer from Depop’s Selling Hub.',
        { code: 'not_captured' });
    }
    return { like_count: 0 };
  }
  if (!/^\d{1,6}$/.test(String(row.offer_count ?? ''))) throw depopOfferDrift('offers list');
  return { like_count: Number(row.offer_count) };
}

function depopOfferPreview(body, product) {
  const recipient = Number(body?.offer_recipient_id);
  const others = String(body?.offer_additional_recipients_count ?? '');
  const lowest = depopOfferCents(body?.sellers_lowest_offer_value);
  const current = body?.prices?.current_price?.price_break_down?.item_price;
  const currentCents = current == null ? null : depopOfferCents(current);
  if (String(body?.product_id) !== String(product.id) || !Number.isSafeInteger(recipient) || recipient <= 0 || !/^\d{1,6}$/.test(others)
      || lowest == null || (current != null && currentCents == null)) {
    throw depopOfferDrift('send-offer preview');
  }
  if (String(body.price_currency || '').toUpperCase() !== 'USD') throw marketplaceError('Anteroom sends Depop offers on listings priced in USD.');
  return { recipient_id: recipient, reach: 1 + Number(others), lowest_cents: lowest, current_cents: currentCents };
}

async function depopOfferState(requests, slug, deadline) {
  const { data: me } = await depopOfferCall('GET', '/presentation/api/v1/users/me/', deadline, 'account');
  const { data: product } = await depopOfferCall('GET', `/presentation/api/v1/products/by-slug/${encodeURIComponent(slug)}/`, deadline, 'read');
  if (String(product?.slug ?? '') !== slug) throw marketplaceError('Depop did not return this listing.', { code: 'schema_drift' });
  if (!/^\d{1,15}$/.test(String(product.id ?? ''))) throw marketplaceError('Depop did not return this listing’s ID.', { code: 'schema_drift' });
  const owner = product.user_id ?? product.seller?.id ?? product.user?.id;
  if (owner == null || String(owner) !== String(me?.id)) throw marketplaceError('This Depop listing belongs to another account.');
  if (product.status !== 'STATUS_ONSALE' || product.sold === true) throw marketplaceError('This Depop listing has sold or is not on sale.');
  if (depopImportVariants(product).length > 1) throw marketplaceError('Depop does not send offers on listings with several sizes.');
  const currency = String(product.price_currency ?? product.pricing?.currency_name ?? product.currency ?? '').toUpperCase();
  const price = Math.round((depopProductPrice(product) || 0) * 100);
  if (currency !== 'USD' || !(price > 0)) throw marketplaceError('Anteroom sends Depop offers on listings priced in USD.', { code: 'schema_drift' });
  const { like_count: likes } = requests.likers.read((await depopOfferCall('GET', requests.likers.path(product), deadline, 'likers')).data, product);
  if (!likes) return { product, price, likes: 0, highest: centOffer(price, DEPOP_OFFER_MIN_PERCENT) };
  const preview = requests.preview.read((await depopOfferCall('GET', requests.preview.path(product), deadline, 'offer-preview')).data, product);
  // A marked-down item is offered against its current price, as Depop's preview shows it.
  const basis = preview.current_cents ? Math.min(price, preview.current_cents) : price;
  return { product, price, likes, preview, lowest: preview.lowest_cents, highest: centOffer(basis, DEPOP_OFFER_MIN_PERCENT) };
}

async function checkDepopPromotion(payload, deadline) {
  const requests = pendingOfferRequests('depop');
  const state = await depopOfferState(requests, payload.listing_id, deadline);
  return offerFacts(state, Number(payload.discount_percent), centOffer);
}

async function offerDepop(payload, deadline) {
  const offer = checkedOffer(payload, false);
  const requests = pendingOfferRequests('depop');
  const state = await depopOfferState(requests, payload.listing_id, deadline);
  if (state.price !== Number(payload.price_cents)) throw marketplaceError('The Depop price changed since your review. Review the offer again.');
  if (!(state.likes > 0) || !state.preview) throw marketplaceError('This Depop listing has no likers to send an offer to.');
  if (offer > state.highest) throw marketplaceError('Depop needs offers at least 5% below the price. Review the offer again.');
  if (state.lowest && offer < state.lowest) {
    throw marketplaceError(`Depop allows offers of at least $${(state.lowest / 100).toFixed(2)} on this item. Review the offer again.`);
  }
  // One request, as Depop's own "Send offer to all likers" sends it. Never repeated, never one per liker.
  const response = await depopOfferCall(requests.offer.method, requests.offer.path(state.product), deadline, 'offer',
    { body: requests.offer.body(state.preview, offer), expect: 'text' });
  if (!requests.offer.confirmed(response)) throw marketplaceError('Depop did not confirm the offer.', { code: 'uncertain', rejected: false });
  return { ok: true, state: 'done', recipient_count: state.preview.reach };
}

const MARKETPLACE_PROMOTIONS = {
  poshmark: { id: /^[a-f0-9]{24}$/, check: checkPoshmarkPromotion, share: sharePoshmark, offer: offerPoshmark },
  mercari: { id: /^m\d{6,20}$/, actions: ['offer'], check: checkMercariPromotion, offer: offerMercari },
  depop: { id: /^[a-z0-9][a-z0-9-]{2,120}$/, actions: ['offer'], check: checkDepopPromotion, offer: offerDepop },
};

function promotionRefusal(error) {
  // Poshmark asks for a captcha before sharing for some accounts.
  if (/captcha|suspectedbot/i.test(String(error?.marketplace_type || ''))) {
    error = marketplaceError('Poshmark asked for a check before sharing. Share one listing on Poshmark yourself, then try again.',
      { code: 'challenge', marketplace_message: error.marketplace_message });
  }
  // A write whose answer was lost is uncertain; everything else is a refusal.
  return marketplaceFailure(error, error?.code !== 'uncertain' && error?.rejected !== false);
}

async function checkMarketplacePromotion(payload) {
  const entry = MARKETPLACE_PROMOTIONS[payload?.marketplace];
  if (!entry || !(entry.actions || ['share', 'offer']).includes(payload.action) || !entry.id.test(String(payload.listing_id || ''))) {
    return marketplaceFailure(marketplaceError('Name a marketplace listing and an action to check.'), true);
  }
  try { return await entry.check(payload, Date.now() + PROMOTION_DEADLINE_MS); }
  catch (error) { return marketplaceFailure(error, true); }
}

async function runMarketplacePromotion(payload) {
  const entry = MARKETPLACE_PROMOTIONS[payload?.marketplace];
  if (!entry || !(entry.actions || ['share', 'offer']).includes(payload.action) || payload.confirm !== true
      || !entry.id.test(String(payload.listing_id || '')) || !/^[a-z0-9-]{6,64}$/i.test(String(payload.action_id || ''))) {
    return marketplaceFailure(marketplaceError('Confirm a share or offer on a named marketplace listing.'), true);
  }
  try { return await entry[payload.action](payload, Date.now() + PROMOTION_DEADLINE_MS); }
  catch (error) { return promotionRefusal(error); }
}

async function handleMarketplacePromotion(msg) {
  const payload = msg.payload || {};
  if (msg.type === 'marketplace.promotion_result') {
    const stored = (await chrome.storage.local.get(promotionKey(payload.action_id)))[promotionKey(payload.action_id)];
    sendBrowserResult(msg, stored?.finished ? { finished: true, result: stored.result } : { finished: false, known: Boolean(stored) });
    return;
  }
  // Checks only read, so they never hold or wait for the automation slot.
  if (msg.type === 'marketplace.promotion_check') {
    sendBrowserResult(msg, await checkMarketplacePromotion(payload));
    return;
  }
  if (activeNativeOperation || activeJob || activePatch) {
    sendBrowserResult(msg, marketplaceFailure(marketplaceError('Another automation job is running.'), true));
    return;
  }
  activeNativeOperation = { id: `promotion-${payload.action_id || payload.request_id || ''}`, tabId: null, cancelled: false,
    controller: new AbortController() };
  try {
    const key = promotionKey(payload.action_id);
    const stored = (await chrome.storage.local.get(key))[key];
    if (stored) {
      // Never send the same action twice. An unfinished one is reported, not repeated.
      sendBrowserResult(msg, stored.finished ? stored.result : marketplaceFailure(
        marketplaceError('This action was interrupted. Check the listing on the marketplace before doing it again.'), false));
      return;
    }
    await chrome.storage.local.set({ [key]: { finished: false, at: Date.now(), marketplace: payload.marketplace, action: payload.action } });
    const result = await runMarketplacePromotion(payload);
    await chrome.storage.local.set({ [key]: { finished: true, at: Date.now(), result } });
    sendBrowserResult(msg, result);
  } finally { activeNativeOperation = null; }
}
