// Facebook Marketplace through the seller's signed-in Chrome session. Reads only:
// nothing here changes a Facebook listing.
//
// Facebook's own web client reads and writes listings through /api/graphql/
// with persisted queries (doc_id) that change with Facebook's releases, and
// their names and variables have not been recorded from a signed-in client
// yet, so Anteroom sends none of them. A listing is read from its own item
// page instead. Facebook renders that page with the listing record embedded
// as JSON: is_sold, is_pending, is_live, is_hidden and the seller's account ID.
//
// Facebook is strict about automation. Every read is one GET that is never
// retried, reads are spaced several seconds apart, and a sign-in or browser
// check stops the whole pass.
const FACEBOOK_ORIGIN = 'https://www.facebook.com';
const FACEBOOK_LISTING_ID = /^\d{6,20}$/;
const FACEBOOK_READ_DEADLINE_MS = 30000;
const FACEBOOK_PAGE_LIMIT = 8_000_000;
const FACEBOOK_BLOCK_LIMIT = 4_000_000;
const FACEBOOK_WALK_LIMIT = 400_000;
// One sale pass reads at most this many listings; Anteroom rotates through the rest on later passes.
const FACEBOOK_SALE_BATCH = 6;
const FACEBOOK_SALE_DEADLINE_MS = 75000;

async function facebookAccountId() {
  const cookie = await chrome.cookies.get({ url: `${FACEBOOK_ORIGIN}/`, name: 'c_user' });
  const id = String(cookie?.value || '');
  return /^\d{1,20}$/.test(id) ? id : '';
}

// Facebook keeps a session in two cookies: c_user (the account) and xs (the session).
async function readFacebookSession(now = Date.now()) {
  const [user, session] = await Promise.all([
    chrome.cookies.get({ url: `${FACEBOOK_ORIGIN}/`, name: 'c_user' }),
    chrome.cookies.get({ url: `${FACEBOOK_ORIGIN}/`, name: 'xs' }),
  ]);
  const present = /^\d{1,20}$/.test(String(user?.value || '')) && Boolean(session?.value);
  const times = [user, session].filter(cookie => cookie && !cookie.session && Number.isFinite(cookie.expirationDate))
    .map(cookie => Math.floor(cookie.expirationDate));
  return publicExtensionSession(present, times.length ? Math.min(...times) : null, null, now);
}

// The JSON blocks Facebook embeds in a page as <script type="application/json">.
function facebookPageJson(html) {
  const pattern = /<script\b[^>]*\btype="application\/json"[^>]*>([\s\S]*?)<\/script>/g;
  return [...String(html || '').matchAll(pattern)].map(match => match[1]);
}

// The record for this listing: the object that carries its ID and is_sold.
// Prices, titles, photos and the seller's name are never read out of it.
function facebookListingRecord(html, id) {
  for (const block of facebookPageJson(html)) {
    if (block.length > FACEBOOK_BLOCK_LIMIT || !block.includes(`"${id}"`) || !block.includes('"is_sold"')) continue;
    let data;
    try { data = JSON.parse(block); } catch { continue; }
    const stack = [data];
    for (let seen = 0; stack.length && seen < FACEBOOK_WALK_LIMIT; seen += 1) {
      const node = stack.pop();
      if (!Array.isArray(node) && node.id === id && typeof node.is_sold === 'boolean') {
        const seller = String(node.marketplace_listing_seller?.id ?? '');
        return {
          is_sold: node.is_sold, is_pending: node.is_pending === true, is_live: node.is_live === true,
          is_hidden: node.is_hidden === true, is_draft: node.is_draft === true,
          seller_id: /^\d{1,20}$/.test(seller) ? seller : null,
        };
      }
      for (const value of Array.isArray(node) ? node : Object.values(node)) {
        if (value && typeof value === 'object') stack.push(value);
      }
    }
  }
  return null;
}

function facebookState(record) {
  if (record.is_sold) return 'sold';
  if (record.is_pending) return 'pending';
  if (record.is_draft) return 'draft';
  if (record.is_hidden) return 'hidden';
  return record.is_live ? 'live' : 'not_live';
}

// One listing's state, from its item page. Returns { ok, state, owner }:
// owner is "you", "other", or null when the page does not name the seller.
async function readFacebookListing(id, deadline) {
  const account = await facebookAccountId();
  if (!account) throw marketplaceError('Sign in to Facebook in Chrome, then try again.', { code: 'signed_out' });
  const { data } = await marketplaceFetch(`${FACEBOOK_ORIGIN}/marketplace/item/${id}/`, {
    marketplace: 'facebook', step: 'read', expect: 'text', retry: false, deadline, timeoutMs: 20000,
    headers: { Accept: 'text/html,application/xhtml+xml' },
  });
  const html = String(data || '').slice(0, FACEBOOK_PAGE_LIMIT);
  const record = facebookListingRecord(html, id);
  if (!record) {
    if (/\/checkpoint\//.test(html.slice(0, 200000))) {
      throw marketplaceError('Facebook asked for a security check. Open Facebook in Chrome, finish the check, then try again.', { code: 'challenge' });
    }
    if (/id="login_form"|name="login_source"/.test(html)) {
      throw marketplaceError('Facebook asked to sign in again. Sign in to Facebook in Chrome.', { code: 'signed_out' });
    }
    throw marketplaceError('Facebook\'s page did not include this listing. It may have been deleted, or Facebook changed its page.',
      { code: 'schema_drift' });
  }
  const owner = record.seller_id ? (record.seller_id === account ? 'you' : 'other') : null;
  return { ok: true, state: facebookState(record), owner };
}

async function facebookPause() {
  const ms = 3000 + Math.floor(Math.random() * 4000);
  await new Promise(resolve => {
    const timer = setTimeout(resolve, ms);
    if (timer === 0) resolve();
  });
}

// Sold listings among the first few IDs. A listing whose page cannot be read is
// skipped; a sign-in, browser check or rate limit stops the pass.
async function facebookSales(ids) {
  const deadline = Date.now() + FACEBOOK_SALE_DEADLINE_MS;
  const sales = [];
  let read = 0;
  for (const [index, id] of ids.slice(0, FACEBOOK_SALE_BATCH).entries()) {
    if (index > 0) await facebookPause();
    if (Date.now() + 25000 > deadline) break;
    let listing;
    try {
      listing = await readFacebookListing(id, deadline);
    } catch (error) {
      if (['signed_out', 'challenge', 'rate_limited'].includes(error.code)) throw error;
      continue;
    }
    read += 1;
    if (listing.state === 'sold' && listing.owner !== 'other') sales.push({ listing_id: id });
  }
  if (!read) throw new Error('Facebook listings unreadable');
  return sales;
}

async function handleMarketplaceListingState(msg) {
  const payload = msg.payload || {};
  const id = String(payload.listing_id || '');
  if (payload.marketplace !== 'facebook' || !FACEBOOK_LISTING_ID.test(id)) {
    sendBrowserResult(msg, marketplaceFailure(marketplaceError('Name a Facebook listing to check.'), true));
    return;
  }
  if (activeNativeOperation || activeJob || activePatch) {
    sendBrowserResult(msg, marketplaceFailure(marketplaceError('Another automation job is running.'), true));
    return;
  }
  try {
    sendBrowserResult(msg, await readFacebookListing(id, Date.now() + FACEBOOK_READ_DEADLINE_MS));
  } catch (error) {
    sendBrowserResult(msg, marketplaceFailure(error, true));
  }
}
