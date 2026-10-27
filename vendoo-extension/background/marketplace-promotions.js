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
  // Space shares the way a person would, across batches and restarts.
  const last = Number((await chrome.storage.local.get(POSHMARK_LAST_SHARE_KEY))[POSHMARK_LAST_SHARE_KEY]) || 0;
  const wait = last + POSHMARK_SHARE_GAP_MS + Math.floor(Math.random() * POSHMARK_SHARE_JITTER_MS) - Date.now();
  if (wait > 0) await marketplaceSleep(Math.min(wait, POSHMARK_SHARE_GAP_MS + POSHMARK_SHARE_JITTER_MS));
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

const MARKETPLACE_PROMOTIONS = {
  poshmark: { id: /^[a-f0-9]{24}$/, check: checkPoshmarkPromotion, share: sharePoshmark, offer: offerPoshmark },
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
  if (!entry || !['share', 'offer'].includes(payload.action) || !entry.id.test(String(payload.listing_id || ''))) {
    return marketplaceFailure(marketplaceError('Name a marketplace listing and an action to check.'), true);
  }
  try { return await entry.check(payload, Date.now() + PROMOTION_DEADLINE_MS); }
  catch (error) { return marketplaceFailure(error, true); }
}

async function runMarketplacePromotion(payload) {
  const entry = MARKETPLACE_PROMOTIONS[payload?.marketplace];
  if (!entry || !['share', 'offer'].includes(payload.action) || payload.confirm !== true
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
