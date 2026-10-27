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
// pages. A marketplace whose signed-in requests have not been captured (both
// sites refuse a plain fetch) keeps null entries, and its checks and offers
// refuse before any request is made (docs/vendoo-feature-parity.md, gap 4).
// Mercari (help article 347): at least 10% below the item's lowest price so
// far, whole dollars, at least $1, to up to 50 recent likers, for 24 hours.
// Its offer screen (recorded 2026-10-27) asks offerToLikerable whether the
// item can take one, then offerToLikerInit for the allowed range in cents,
// which already applies the lowest-price rule, and sends createOfferToLikers.
// offerPrice is in cents like every other Mercari price (item.price, the
// draft and edit price, minPriceForAutoPriceDrop). Mercari's client also
// adds x-socure-device-token, a device-risk token from Socure's in-page SDK,
// to every API call once that SDK loads; the worker has no page, sends none,
// as its draft, publish and edit calls already do.
// Depop (Send Offer help): to likers in the seller's country, one active offer
// per buyer, not on listings with several sizes, shipping not included. Its
// partner Selling API requires at least 5% off; the web client is assumed to
// be no looser.
const PENDING_OFFER_REQUESTS = {
  mercari: {
    likers: { operationName: 'offerToLikerable', sha256Hash: '37bcc049bd1bf9241387cf44f4a2742a653f2e3dca8150c26d00aa27e30cc825',
      variables: id => ({ itemId: id }),
      read: data => ({ eligible: data?.offer?.offerToLikerable, price_cents: data?.item?.price, like_count: data?.item?.numLikes }) },
    bounds: { operationName: 'offerToLikerInit', sha256Hash: 'babbde0d58f82b3803f24247c0e9f007688c75e3c3b9cbef96a7b33f055cc64a',
      variables: id => ({ itemId: id }),
      read: data => ({ lowest_cents: data?.offerToLikerBounds?.lowerLimitCents, highest_cents: data?.offerToLikerBounds?.higherLimitCents }) },
    offer: { operationName: 'createOfferToLikers', sha256Hash: '00c320abe9a24e864ad765b06b0276517b423e12f66203a27ffd9edd95ff36e6',
      variables: (id, offerCents) => ({ input: { itemId: id, offerPrice: offerCents } }),
      created: data => data?.createOfferToLikers?.numOffersCreated },
  },
  // likers: { path: product => string, read: body => ({ like_count }) }
  // offer:  { method, path: product => string, body: (product, offerCents) => ({}), confirmed: body => boolean }
  depop: { likers: null, offer: null },
};
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

// ``lowest`` is the marketplace's own lower limit when it gives one; $1 otherwise.
function offerFacts(state, percent, round) {
  const lowest = state.lowest || 100;
  const minimum = Math.max(1, Math.ceil(100 - state.highest * 100 / state.price));
  const maximum = state.lowest ? Math.min(99, Math.floor(100 - lowest * 100 / state.price)) : 99;
  const offer = Number.isInteger(percent) ? round(state.price, percent) : 0;
  return { ok: true, price_cents: state.price, like_count: state.likes, minimum_discount_percent: minimum, maximum_discount_percent: maximum,
    shipping_discounts: [], offer_price_cents: percent >= minimum && percent <= maximum && offer >= lowest && offer <= state.highest ? offer : null };
}

function checkedOffer(payload, wholeDollars) {
  const offer = Number(payload.offer_price_cents);
  const floor = Number(payload.floor_cents);
  if (!Number.isInteger(offer) || offer < 100 || (wholeDollars && offer % 100) || !Number.isInteger(floor) || floor <= 0 || offer < floor) {
    throw marketplaceError('This offer is below your floor or not a price the marketplace accepts. Review it again.');
  }
  return offer;
}

function mercariPersisted(request, variables) {
  return { operationName: request.operationName, variables, extensions: { persistedQuery: { version: 1, sha256Hash: request.sha256Hash } } };
}

// The item, its likers and Mercari's own offer range, all read before anything is sent.
async function mercariOfferState(requests, id, deadline) {
  const session = await mercariCurrentSession(deadline, true);
  const data = await mercariRequest({ operationName: 'sellFetchItemsDetails', variables: { itemId: id }, query: MERCARI_ITEM_DETAILS_QUERY },
    { deadline, retry: true });
  const item = data?.item;
  if (String(item?.itemId ?? '') !== id) throw marketplaceError('Mercari did not return this listing.', { code: 'schema_drift' });
  if (String(item.seller?.id) !== String(session.user.userId)) throw marketplaceError('This Mercari listing belongs to another account.');
  if (item.status !== 'on_sale') throw marketplaceError('This Mercari listing has sold or is not on sale.');
  const price = Number(item.price);
  if (!(price > 0)) throw marketplaceError('Mercari did not return this listing’s price.', { code: 'schema_drift' });
  const likers = requests.likers.read(await mercariRequest(mercariPersisted(requests.likers, requests.likers.variables(id)),
    { deadline, retry: true }));
  if (typeof likers.eligible !== 'boolean' || Number(likers.price_cents) !== price || !Number.isInteger(likers.like_count)) {
    throw marketplaceError('Mercari’s offer screen changed. Anteroom needs an update before sending offers.', { code: 'schema_drift' });
  }
  if (!likers.eligible) throw marketplaceError('Mercari says this listing cannot get an offer to likers right now.');
  const bounds = requests.bounds.read(await mercariRequest(mercariPersisted(requests.bounds, requests.bounds.variables(id)),
    { deadline, retry: true }));
  const lower = Number(bounds.lowest_cents), higher = Number(bounds.highest_cents);
  // Cents below the price; anything else means the units or the rule changed.
  if (!Number.isInteger(lower) || !Number.isInteger(higher) || lower < 100 || higher < lower || higher >= price) {
    throw marketplaceError('Mercari’s offer limits changed. Anteroom needs an update before sending offers.', { code: 'schema_drift' });
  }
  return { price, likes: likers.like_count, lowest: Math.ceil(lower / 100) * 100, highest: Math.floor(higher / 100) * 100 };
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
  if (offer < state.lowest || offer > state.highest) {
    throw marketplaceError(`Mercari takes offers to likers from $${state.lowest / 100} to $${state.highest / 100} on this listing. Review the offer again.`);
  }
  // Sent once: a refused session is not read again and resent.
  const data = await mercariRequest(mercariPersisted(requests.offer, requests.offer.variables(payload.listing_id, offer)),
    { deadline, resend: false });
  const created = requests.offer.created(data);
  if (!Number.isInteger(created) || created < 0) {
    throw marketplaceError('Mercari did not confirm the offer.', { code: 'uncertain', rejected: false });
  }
  if (created === 0) throw marketplaceError('Mercari sent this offer to no one. Its likers may already have an offer.');
  return { ok: true, state: 'done', recipient_count: created };
}

async function depopOfferCall(method, url, deadline, step, body) {
  const { data } = await marketplaceFetch(url.startsWith('https://') ? url : `${DEPOP_WEBAPI}${url}`, { marketplace: 'depop', step, method,
    credentials: 'omit', deadline, headers: { Accept: 'application/json', Authorization: await depopBearer(),
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  return data;
}

async function depopOfferState(requests, slug, deadline) {
  const me = await depopOfferCall('GET', '/presentation/api/v1/users/me/', deadline, 'account');
  const product = await depopOfferCall('GET', `/presentation/api/v1/products/by-slug/${encodeURIComponent(slug)}/`, deadline, 'read');
  if (String(product?.slug ?? '') !== slug) throw marketplaceError('Depop did not return this listing.', { code: 'schema_drift' });
  const owner = product.user_id ?? product.seller?.id ?? product.user?.id;
  if (owner == null || String(owner) !== String(me?.id)) throw marketplaceError('This Depop listing belongs to another account.');
  if (product.status !== 'STATUS_ONSALE' || product.sold === true) throw marketplaceError('This Depop listing has sold or is not on sale.');
  if (depopImportVariants(product).length > 1) throw marketplaceError('Depop does not send offers on listings with several sizes.');
  const currency = String(product.price_currency ?? product.pricing?.currency_name ?? product.currency ?? '').toUpperCase();
  const price = Math.round((depopProductPrice(product) || 0) * 100);
  if (currency !== 'USD' || !(price > 0)) throw marketplaceError('Anteroom sends Depop offers on listings priced in USD.', { code: 'schema_drift' });
  const likers = requests.likers.read(await depopOfferCall('GET', requests.likers.path(product), deadline, 'likers'));
  return { product, price, likes: Number(likers?.like_count) || 0, highest: centOffer(price, DEPOP_OFFER_MIN_PERCENT) };
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
  if (!(state.likes > 0)) throw marketplaceError('This Depop listing has no likers to send an offer to.');
  if (offer > state.highest) throw marketplaceError('Depop needs offers at least 5% below the price. Review the offer again.');
  const body = await depopOfferCall(requests.offer.method, requests.offer.path(state.product), deadline, 'offer',
    requests.offer.body(state.product, offer));
  if (!requests.offer.confirmed(body)) throw marketplaceError('Depop did not confirm the offer.', { code: 'uncertain', rejected: false });
  return { ok: true, state: 'done', recipient_count: state.likes };
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
