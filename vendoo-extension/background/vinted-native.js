// Vinted drafts, publishing and live edits from the extension worker, with no marketplace tab.
// The calls are the ones Vinted's own sell form makes (read 2026-10-26):
//   POST /api/v2/item_upload/drafts                         save a draft
//   GET  /api/v2/item_upload/items/{id}                     read it back
//   POST /api/v2/item_upload/drafts/{id}/completion         publish that draft
//   PUT  /api/v2/item_upload/items/{id}                     save an edit of a live item
//   GET  /api/v2/wardrobe/{user}/items                      the seller's closet
// Cookies stay in Chrome. A lost reply is read back and never creates a second draft.

const VINTED_ORIGIN = 'https://www.vinted.com';
const VINTED_MAX_PHOTOS = 20;
// Vinted's web access token lasts an hour; Chrome keeps a week-long refresh
// token beside it. The site renews the hour at /session-refresh, so the
// extension asks the same route rather than reading an hour-old expiry as a
// signed-out seller.
const VINTED_REFRESH_URL = `${VINTED_ORIGIN}/web/api/auth/refresh`;
const VINTED_REFRESH_MARGIN_MS = 5 * 60 * 1000;

function vintedDraftUrl(id) {
  return `${VINTED_ORIGIN}/items/${id}/edit`;
}

function vintedPublicUrl(id) {
  return `${VINTED_ORIGIN}/items/${id}`;
}

function vintedDraftId(draftUrl) {
  return String(draftUrl || '').match(/^https:\/\/www\.vinted\.com\/items\/([1-9]\d{0,14})\/edit\/?$/)?.[1] || '';
}

function vintedAmount(value) {
  const raw = value && typeof value === 'object' ? (value.amount ?? value.price) : value;
  const amount = Number(raw);
  return Number.isFinite(amount) ? amount : null;
}

function vintedFlatten(options, out = []) {
  for (const option of options || []) {
    if (!option || typeof option !== 'object') continue;
    if (Array.isArray(option.options) && option.options.length) vintedFlatten(option.options, out);
    else if (option.id != null && option.title) out.push({ id: Number(option.id), title: String(option.title) });
  }
  return out.filter(option => Number.isInteger(option.id) && option.id > 0);
}

function vintedMatch(options, wanted) {
  const text = marketplaceText(wanted).toLowerCase();
  if (!text) return null;
  const exact = options.filter(option => marketplaceText(option.title).toLowerCase() === text);
  return exact.length === 1 ? exact[0] : null;
}

function vintedSizeText(value) {
  return marketplaceText(value).toLowerCase().replace(/\s*\/\s*/g, ' / ').replace(/\s+/g, ' ');
}

// Vinted writes its own clothing sizes as "S / US 4-6" and its waists as
// "W32"; Anteroom stores "6", "S / 6" or "M", and its bottoms mapping still
// carries Vendoo's older letter. The number a seller typed is the size they
// mean, so it decides; the letter is used when there is no number.
function vintedSizeNumber(title, digits) {
  const waist = /^w?(\d{1,2})$/.exec(title);
  if (waist) return waist[1] === digits;
  const range = / \/ us (\d{1,2})(?:-(\d{1,2}))?$/.exec(title);
  if (!range) return false;
  if (!range[2]) return range[1] === digits;
  return Number(digits) >= Number(range[1]) && Number(digits) <= Number(range[2]);
}

function vintedSizeMatch(options, wanted) {
  const text = vintedSizeText(wanted);
  if (!text) return null;
  const exact = options.filter(option => vintedSizeText(option.title) === text);
  if (exact.length === 1) return exact[0];
  const [first, second] = text.replace(/^us /, '').split(' / ');
  const digits = (second ?? first).match(/^\d{1,2}$/)?.[0] || '';
  if (digits) {
    // Two options answering the same number would be a guess, not a match.
    const byNumber = options.filter(option => vintedSizeNumber(vintedSizeText(option.title), digits));
    return byNumber.length === 1 ? byNumber[0] : null;
  }
  const letter = /^[a-z]{1,4}$/.test(first) ? first : '';
  if (!letter) return null;
  const byLetter = options.filter(option => (/^([a-z]{1,4})(?: \/|$)/.exec(vintedSizeText(option.title))?.[1] || '') === letter);
  return byLetter.length === 1 ? byLetter[0] : null;
}

// Package sizes carry a stable code (SMALL, MEDIUM, LARGE) beside their title,
// which Vinted translates. Match the app's English name to the code.
function vintedPackageMatch(options, wanted) {
  const exact = vintedMatch(options, wanted);
  if (exact) return exact;
  const key = value => marketplaceText(value).toLowerCase().replace(/[\s_-]+/g, '')
    .replace(/^extrasmall$/, 'xsmall').replace(/^extralarge$/, 'xlarge');
  const wantedKey = key(wanted);
  if (!wantedKey) return null;
  const matches = options.filter(option => key(option.code) === wantedKey);
  return matches.length === 1 ? matches[0] : null;
}

function vintedFindCatalog(nodes, parts, index = 0) {
  const want = parts[index];
  const matches = (nodes || []).filter(node => marketplaceText(node?.title).toLowerCase() === want);
  if (matches.length !== 1) return null;
  if (index === parts.length - 1) {
    const id = Number(matches[0].id);
    return Number.isInteger(id) && id > 0 ? id : null;
  }
  return vintedFindCatalog(matches[0].catalogs || matches[0].children || [], parts, index + 1);
}

function vintedView(item) {
  if (!item || typeof item !== 'object' || item.id == null) return null;
  const photos = Array.isArray(item.assigned_photos) ? item.assigned_photos : Array.isArray(item.photos) ? item.photos : null;
  const photoIds = photos ? photos.map(photo => Number(photo?.id)).filter(photoId => Number.isInteger(photoId) && photoId > 0) : null;
  const condition = (item.item_attributes || []).find(attribute => attribute?.code === 'condition');
  const conditionId = condition?.ids?.[0] ?? item.status_id;
  const sizeId = (item.item_attributes || []).find(attribute => attribute?.code === 'size')?.ids?.[0] ?? item.size_id;
  return {
    id: String(item.id),
    title: item.title ?? null,
    description: item.description ?? null,
    price: vintedAmount(item.price ?? item.price_numeric),
    catalog_id: item.catalog_id != null ? Number(item.catalog_id) : null,
    condition_id: conditionId != null ? Number(conditionId) : null,
    size_id: sizeId != null ? Number(sizeId) : null,
    photo_count: photos ? photos.length : null,
    photo_ids: photoIds,
    is_draft: item.is_draft === true ? true : item.is_draft === false ? false : null,
    is_closed: item.is_closed === true,
    is_hidden: item.is_hidden === true,
    is_reserved: item.is_reserved === true,
  };
}

function vintedDifferences(view, expected) {
  const differ = [];
  if (view.title == null || marketplaceText(view.title) !== marketplaceText(expected.title)) differ.push('title');
  if (expected.description != null && (view.description == null || marketplaceText(view.description) !== marketplaceText(expected.description))) differ.push('description');
  if (expected.price != null && view.price !== expected.price) differ.push('price');
  if (expected.catalog_id != null && view.catalog_id !== expected.catalog_id) differ.push('category');
  if (expected.condition_id != null && view.condition_id !== expected.condition_id) differ.push('condition');
  if (expected.size_id != null && view.size_id !== expected.size_id) differ.push('size');
  if (expected.photo_count != null && view.photo_count !== expected.photo_count) differ.push('photos');
  return differ;
}

function vintedLive(view) {
  return Boolean(view && view.is_draft === false && !view.is_closed && !view.is_hidden && !view.is_reserved);
}

function vintedAllowed(method, url, action, confirm) {
  let parsed;
  try { parsed = new URL(url); } catch { return false; }
  const path = parsed.pathname;
  if (parsed.hostname === 'api.www.vinted.com') {
    return method === 'GET' && /^\/shipping-estimation\/external\/catalogs\/[1-9]\d{0,14}\/package_sizes$/.test(path);
  }
  if (parsed.hostname !== 'www.vinted.com') return false;
  if (method === 'POST' && path === '/web/api/auth/refresh') return true;
  if (method === 'GET' && (path === '/items/new' || path === '/api/v2/users/current' || path === '/api/v2/item_upload/catalogs'
      || path === '/api/v2/item_upload/colors' || path === '/api/v2/item_upload/brands' || path === '/api/v2/item_upload/size_groups'
      || /^\/api\/v2\/item_upload\/items\/[1-9]\d{0,14}$/.test(path) || /^\/api\/v2\/items\/[1-9]\d{0,14}$/.test(path)
      || /^\/api\/v2\/wardrobe\/[1-9]\d{0,14}\/items$/.test(path))) return true;
  if (method === 'POST' && path === '/api/v2/item_upload/attributes') return true;
  if (action === 'save_draft' && method === 'POST' && (path === '/api/v2/photos' || path === '/api/v2/item_upload/drafts')) return true;
  if (action === 'save_draft' && method === 'PUT' && /^\/api\/v2\/item_upload\/drafts\/[1-9]\d{0,14}$/.test(path)) return true;
  if (action === 'update') return confirm === true && method === 'PUT' && /^\/api\/v2\/item_upload\/items\/[1-9]\d{0,14}$/.test(path);
  return action === 'publish' && confirm === true && method === 'POST'
    && /^\/api\/v2\/item_upload\/drafts\/[1-9]\d{0,14}\/completion$/.test(path);
}

async function vintedAnonId() {
  try {
    const cookie = await chrome.cookies.get({ url: `${VINTED_ORIGIN}/`, name: 'anon_id' });
    const value = String(cookie?.value || '');
    return /^[A-Za-z0-9-]{8,80}$/.test(value) ? value : '';
  } catch {
    return '';
  }
}

function vintedTokenExpiry(token) {
  try {
    const part = String(token || '').split('.')[1];
    if (!part) return 0;
    const padded = part.replace(/-/g, '+').replace(/_/g, '/');
    const claims = JSON.parse(atob(padded + '='.repeat((4 - (padded.length % 4)) % 4)));
    const exp = Number(claims?.exp);
    return Number.isFinite(exp) && exp > 0 ? Math.floor(exp) : 0;
  } catch {
    return 0;
  }
}

// One of Vinted's token cookies, as Chrome holds it. The value never leaves the worker.
async function vintedTokenCookie(name) {
  let cookie = null;
  try { cookie = await chrome.cookies.get({ url: `${VINTED_ORIGIN}/`, name }); } catch { /* no cookie jar here */ }
  const token = String(cookie?.value || '');
  return { cookie, token, expires_at: vintedTokenExpiry(token) };
}

// Renews the hour-long access token with the refresh token Chrome keeps. The
// answer sets both cookies, and its body carries the same access token.
async function vintedRefreshAccess(deadline) {
  let body = null;
  try {
    ({ data: body } = await marketplaceFetch(VINTED_REFRESH_URL, {
      marketplace: 'vinted', step: 'session', method: 'POST', credentials: 'include', expect: 'json',
      deadline, headers: { Accept: 'application/json, text/plain, */*' }, retry: false,
    }));
  } catch (error) {
    // A refused renewal is a sign-in problem; anything else keeps its own words.
    if (['network', 'challenge', 'rate_limited'].includes(error?.code)) throw error;
    throw marketplaceError('Reconnect Vinted in Anteroom first.', { code: 'signed_out', status: error?.status ?? null });
  }
  if (vintedTokenExpiry(body?.access_token)) return;
  if ((await vintedTokenCookie('access_token_web')).expires_at) return;
  throw marketplaceError('Reconnect Vinted in Anteroom first.', { code: 'signed_out' });
}

// Refreshes only when the hour is nearly up, so a token that still works can
// finish the call. `force` retries once after Vinted says the token is invalid.
// Returns whether a new token was actually fetched.
async function vintedKeepSignedIn(deadline, { force = false } = {}) {
  const { expires_at } = await vintedTokenCookie('access_token_web');
  const now = Date.now();
  if (!force && expires_at * 1000 - now > VINTED_REFRESH_MARGIN_MS) return false;
  try {
    await vintedRefreshAccess(deadline);
    return true;
  } catch (error) {
    if (!force && expires_at * 1000 > now) return false;
    throw error;
  }
}

async function vintedSession(deadline) {
  const renewed = await vintedKeepSignedIn(deadline);
  const page = await marketplaceFetch(`${VINTED_ORIGIN}/items/new`, {
    marketplace: 'vinted', step: 'session', expect: 'text', credentials: 'include', deadline,
  });
  const csrf = vintedCsrfToken(page.data);
  const anon = await vintedAnonId();
  if (!csrf) throw marketplaceError('Reconnect Vinted in Anteroom first.', { code: 'signed_out' });
  const headers = {
    Accept: 'application/json, text/plain, */*',
    'x-csrf-token': csrf,
    'x-enable-multiple-size-groups': 'true',
    // Anteroom's own choices are English; ask Vinted for the same names.
    Locale: 'en-US',
    ...(anon ? { 'x-anon-id': anon } : {}),
  };
  const call = async (method, url, { step, body, retry, action, confirm }) => {
    if (!vintedAllowed(method, url, action, confirm)) throw marketplaceError('Unsupported Vinted action.');
    const json = body !== undefined && !(body instanceof FormData);
    const { data } = await marketplaceFetch(url, {
      marketplace: 'vinted', step, method, credentials: 'include', deadline,
      retry: retry ?? method === 'GET', expect: 'json',
      headers: { ...headers, ...(json ? { 'Content-Type': 'application/json', 'x-upload-form': 'true' } : {}) },
      ...(body === undefined ? {} : { body: json ? JSON.stringify(body) : body }),
    });
    return data;
  };
  const currentUser = () => call('GET', `${VINTED_ORIGIN}/api/v2/users/current`, { step: 'session' });
  const signedOut = error => error?.code === 'signed_out' || error?.status === 401 || error?.status === 403;
  let account;
  try {
    account = await currentUser();
  } catch (error) {
    if (!signedOut(error)) throw error;
    // The hour can turn over between the check above and this call.
    if (!renewed) await vintedKeepSignedIn(deadline, { force: true });
    try {
      account = await currentUser();
    } catch (retry) {
      if (signedOut(retry)) throw marketplaceError('Reconnect Vinted in Anteroom first.', { code: 'signed_out' });
      throw retry;
    }
  }
  const userId = vintedAccountId(account);
  if (!userId) throw marketplaceError('Reconnect Vinted in Anteroom first.', { code: 'signed_out' });
  const currency = String(account?.user?.currency || 'USD').toUpperCase();
  return { call, userId, currency: /^[A-Z]{3}$/.test(currency) ? currency : 'USD' };
}

async function vintedRead(call, id) {
  let found = null;
  for (const path of [`/api/v2/item_upload/items/${id}`, `/api/v2/items/${id}`]) {
    try {
      const body = await call('GET', `${VINTED_ORIGIN}${path}`, { step: 'read' });
      const view = vintedView(body?.item || body?.draft || body);
      if (!view || view.id !== String(id)) continue;
      if (!found) found = view;
      else {
        found = {
          ...found,
          ...view,
          photo_ids: view.photo_ids?.length ? view.photo_ids : found.photo_ids,
          photo_count: view.photo_ids?.length ? view.photo_count : found.photo_count,
          is_draft: view.is_draft != null ? view.is_draft : found.is_draft,
        };
      }
      if (found.photo_ids?.length && found.is_draft != null) return found;
    } catch (error) {
      if (error.status !== 404) throw error;
    }
  }
  return found;
}

async function vintedWardrobe(call, userId) {
  const found = [];
  for (let page = 1; page <= 8; page += 1) {
    const body = await call('GET', `${VINTED_ORIGIN}/api/v2/wardrobe/${userId}/items?page=${page}&per_page=96&order=newest_first`, { step: 'read' });
    if (!Array.isArray(body?.items)) throw marketplaceError('Vinted answered in a way Anteroom does not recognise.', { code: 'schema_drift' });
    found.push(...body.items);
    const total = Number(body.pagination?.total_pages) || page;
    if (page >= total || !body.items.length) break;
  }
  return found;
}

function vintedSame(item, expected) {
  const view = vintedView(item);
  if (!view) return false;
  if (marketplaceText(view.title) !== marketplaceText(expected.title)) return false;
  return view.price == null || expected.price == null || view.price === expected.price;
}

async function vintedFields(call, listing, currency) {
  const specifics = listing.vinted_specifics && typeof listing.vinted_specifics === 'object' ? listing.vinted_specifics : {};
  const title = String(marketplaceCopy(listing, 'vinted', 'title') || '').trim();
  const description = String(marketplaceCopy(listing, 'vinted', 'description') || '').trim();
  const price = vintedAmount(specifics.price ?? listing.price);
  const missing = [];
  if (!title) missing.push({ field: 'title', needs: 'a title' });
  if (title.length > 100) missing.push({ field: 'title', needs: 'a title of at most 100 characters' });
  if (!description) missing.push({ field: 'description', needs: 'a description' });
  if (!(price > 0)) missing.push({ field: 'price', needs: 'a price' });
  const parts = String(specifics.category || '').split('>').map(part => marketplaceText(part).toLowerCase()).filter(Boolean);
  if (!parts.length) missing.push({ field: 'vinted_specifics.category', needs: 'a category' });
  if (!String(specifics.condition || '').trim()) missing.push({ field: 'vinted_specifics.condition', needs: 'a condition' });
  if (missing.length) throw marketplaceMissing('vinted', missing);
  const catalogs = await call('GET', `${VINTED_ORIGIN}/api/v2/item_upload/catalogs`, { step: 'category' });
  const catalogId = vintedFindCatalog(catalogs?.catalogs || catalogs, parts);
  if (!catalogId) throw marketplaceMissing('vinted', [{ field: 'vinted_specifics.category', needs: 'a category Vinted offers' }]);
  const attributes = await call('POST', `${VINTED_ORIGIN}/api/v2/item_upload/attributes`, {
    step: 'category', body: { attributes: [{ code: 'category', value: [catalogId] }] }, retry: true,
  });
  const byCode = code => vintedFlatten((attributes?.attributes || []).find(row => row?.code === code)?.configuration?.options);
  let sizes = byCode('size');
  if (!sizes.length) {
    const groups = await call('GET', `${VINTED_ORIGIN}/api/v2/item_upload/size_groups?catalog_ids[]=${catalogId}`, { step: 'category' });
    sizes = vintedFlatten((groups?.size_groups || []).flatMap(group => group?.sizes || group?.options || []));
  }
  const condition = vintedMatch(byCode('condition').concat(byCode('status')), specifics.condition);
  if (!condition) throw marketplaceMissing('vinted', [{ field: 'vinted_specifics.condition', needs: 'a condition Vinted offers' }]);
  // A size the Vinted form leaves blank still has the listing's General size.
  const sizeName = String(marketplaceCopy(listing, 'vinted', 'size') || '').trim();
  const size = vintedSizeMatch(sizes, sizeName);
  if (sizeName && !size) {
    throw marketplaceMissing('vinted', [{ field: 'vinted_specifics.size', needs: 'a size Vinted offers in this category' }]);
  }
  if (!size && sizes.length) throw marketplaceMissing('vinted', [{ field: 'vinted_specifics.size', needs: 'a size' }]);
  let brandId = null;
  let brand = null;
  const brandName = String(marketplaceCopy(listing, 'vinted', 'brand') || '').trim();
  if (brandName) {
    const found = await call('GET', `${VINTED_ORIGIN}/api/v2/item_upload/brands?keyword=${encodeURIComponent(brandName)}&category_id=${catalogId}`, { step: 'brand' });
    const match = vintedMatch((found?.brands || []).map(row => ({ id: Number(row.id), title: row.title })), brandName);
    if (!match) throw marketplaceMissing('vinted', [{ field: 'vinted_specifics.brand', needs: 'a brand Vinted offers' }]);
    brandId = match.id;
    brand = match.title;
  }
  const colorIds = [];
  const colorName = String(marketplaceCopy(listing, 'vinted', 'color') || '').trim();
  if (colorName) {
    const colors = await call('GET', `${VINTED_ORIGIN}/api/v2/item_upload/colors`, { step: 'color' });
    const match = vintedMatch((colors?.colors || []).map(row => ({ id: Number(row.id), title: row.title })), colorName);
    if (!match) throw marketplaceMissing('vinted', [{ field: 'vinted_specifics.color', needs: 'a color Vinted offers' }]);
    colorIds.push(match.id);
  }
  const packages = await call('GET', `https://api.www.vinted.com/shipping-estimation/external/catalogs/${catalogId}/package_sizes`, { step: 'package' });
  const packageSize = vintedPackageMatch((packages?.package_sizes || []).map(row => ({
    id: Number(row.id), title: row.title || row.name || row.code, code: row.code || '',
  })), specifics.packageSize || 'Medium');
  if (!packageSize) throw marketplaceMissing('vinted', [{ field: 'vinted_specifics.packageSize', needs: 'a package size Vinted offers' }]);
  const itemAttributes = [{ code: 'condition', ids: [condition.id] }];
  if (size) itemAttributes.push({ code: 'size', ids: [size.id] });
  return {
    title, description, price, currency, catalog_id: catalogId, condition_id: condition.id, size_id: size ? size.id : null,
    brand_id: brandId, brand, color_ids: colorIds, package_size_id: packageSize.id, item_attributes: itemAttributes,
  };
}

function vintedForm(fields, photos, sessionId, id = null) {
  return {
    id, currency: fields.currency, temp_uuid: sessionId, title: fields.title, description: fields.description,
    brand_id: fields.brand_id, brand: fields.brand, size_id: fields.size_id, catalog_id: fields.catalog_id,
    isbn: null, is_unisex: false, status_id: fields.condition_id, price: fields.price,
    package_size_id: fields.package_size_id, shipment_prices: { domestic: null, international: null },
    color_ids: fields.color_ids, assigned_photos: photos, item_attributes: fields.item_attributes,
    manufacturer: null, manufacturer_labelling: null,
  };
}

function vintedExpected(fields, photoCount) {
  return {
    title: fields.title, description: fields.description, price: fields.price, catalog_id: fields.catalog_id,
    condition_id: fields.condition_id, size_id: fields.size_id, photo_count: photoCount,
  };
}

async function vintedUploadPhotos(call, jobId, photos, sessionId, deadline) {
  const assigned = [];
  for (const photo of photos) {
    const loaded = await nativePhoto(jobId, photo, deadline);
    const bytes = Uint8Array.from(atob(loaded.base64), char => char.charCodeAt(0));
    const form = new FormData();
    form.append('photo[type]', 'item');
    form.append('photo[temp_uuid]', sessionId);
    form.append('photo[file]', new Blob([bytes], { type: loaded.mime_type || 'image/jpeg' }), 'photo.jpg');
    const uploaded = await call('POST', `${VINTED_ORIGIN}/api/v2/photos`, { step: 'photo', body: form, retry: true, action: 'save_draft' });
    const id = Number(uploaded?.id ?? uploaded?.photo?.id);
    if (!Number.isInteger(id) || id <= 0) {
      throw marketplaceError('Vinted did not confirm a photo. Check the draft before trying again.', { code: 'schema_drift', rejected: false });
    }
    assigned.push({ id, orientation: Number(uploaded?.orientation) || 0 });
  }
  return assigned;
}

const VINTED_LIVE_READ_WAITS_MS = [0, 700, 1500];

function vintedColorIds(item) {
  if (Array.isArray(item?.color_ids)) return item.color_ids.map(Number).filter(id => id > 0);
  return [item?.color1_id, item?.color2_id].map(Number).filter(id => id > 0);
}

function vintedSameValue(a, b) {
  if (Array.isArray(a) || Array.isArray(b)) {
    const left = (a || []).map(Number).sort(), right = (b || []).map(Number).sort();
    return left.length === right.length && left.every((value, index) => value === right[index]);
  }
  if (typeof a === 'string' || typeof b === 'string') return marketplaceText(a ?? '') === marketplaceText(b ?? '');
  return (a ?? null) === (b ?? null);
}

// A live item is edited the way Vinted's own edit page saves it: one PUT of
// the whole item, carrying the photos it already has. Brand, colour and
// package size the revision leaves blank, and the attributes Anteroom does
// not set, keep what the live item has. Nothing is sent when nothing differs.
async function updateVintedItem(call, session, payload, listing, recovery, writing) {
  const id = String(payload.listing_id || '');
  if (!/^[1-9]\d{0,14}$/.test(id)) return marketplaceFailure(marketplaceError('A Vinted listing link is required before updating.'), true);
  const listingUrl = vintedPublicUrl(id);
  const specifics = listing.vinted_specifics && typeof listing.vinted_specifics === 'object' ? listing.vinted_specifics : {};
  if (recovery) {
    try {
      const view = await vintedRead(call, id);
      const expected = { title: marketplaceCopy(listing, 'vinted', 'title'), description: marketplaceCopy(listing, 'vinted', 'description'), price: vintedAmount(specifics.price ?? listing.price) };
      const differ = view ? vintedDifferences(view, expected) : ['listing'];
      if (vintedLive(view) && !differ.length) return { ok: true, state: 'published', listing_url: listingUrl };
      return marketplaceFailure(marketplaceError(`Vinted has not confirmed the update${differ.length ? ` (${differ.join(', ')} still differ)` : ''}. Check the live listing.`,
        { code: 'uncertain', rejected: false }), false);
    } catch (error) {
      return marketplaceFailure(error, false);
    }
  }
  const shown = (await call('GET', `${VINTED_ORIGIN}/api/v2/items/${id}`, { step: 'read' }))?.item;
  if (!shown || String(shown.id) !== id) throw marketplaceError('Vinted did not return this listing.', { code: 'schema_drift' });
  if (vintedOwnerId(shown) !== session.userId) throw marketplaceError('This Vinted listing belongs to another account. Nothing was changed.');
  if (!vintedLive(vintedView(shown))) throw marketplaceError('This Vinted listing is not live (sold, reserved or hidden). Nothing was changed.');
  const edit = await call('GET', `${VINTED_ORIGIN}/api/v2/item_upload/items/${id}`, { step: 'read' });
  const raw = edit?.item || edit?.draft || edit;
  const live = vintedView(raw);
  if (!live || live.id !== id) throw marketplaceError('Vinted did not return this listing\'s edit form.', { code: 'schema_drift' });
  const photos = (Array.isArray(raw.photos) && raw.photos.length ? raw.photos : raw.assigned_photos || [])
    .map(photo => ({ id: Number(photo?.id), orientation: Number(photo?.orientation) || 0 }))
    .filter(photo => Number.isInteger(photo.id) && photo.id > 0);
  if (!photos.length) {
    throw marketplaceError('Vinted did not return this listing\'s photos, so nothing was changed. Check the listing on Vinted.', { code: 'schema_drift' });
  }
  const fields = await vintedFields(call, listing, session.currency);
  const named = key => Boolean(String(specifics[key] || '').trim());
  const liveBrand = typeof raw.brand === 'string' ? raw.brand : raw.brand?.title ?? raw.brand_dto?.title ?? null;
  const liveBrandId = Number(raw.brand_id ?? raw.brand_dto?.id) || null;
  const chosen = {
    ...fields,
    brand_id: named('brand') ? fields.brand_id : liveBrandId,
    brand: named('brand') ? fields.brand : liveBrand,
    color_ids: named('color') ? fields.color_ids : vintedColorIds(raw),
    package_size_id: named('packageSize') ? fields.package_size_id : Number(raw.package_size_id) || fields.package_size_id,
    item_attributes: [...(Array.isArray(raw.item_attributes) ? raw.item_attributes : [])
      .filter(attribute => attribute && !['condition', 'size'].includes(attribute.code)), ...fields.item_attributes],
  };
  const current = { title: live.title, description: live.description, price: live.price, catalog_id: live.catalog_id,
    condition_id: live.condition_id, size_id: live.size_id, brand_id: liveBrandId, color_ids: vintedColorIds(raw),
    package_size_id: Number(raw.package_size_id) || null };
  const changed = Object.keys(current).filter(key => !vintedSameValue(current[key], chosen[key]));
  if (!changed.length) return { ok: true, state: 'published', listing_url: listingUrl };
  const sessionId = crypto.randomUUID();
  const item = { ...vintedForm(chosen, photos, sessionId, Number(id)),
    isbn: raw.isbn ?? null, is_unisex: raw.is_unisex === true,
    manufacturer: raw.manufacturer ?? null, manufacturer_labelling: raw.manufacturer_labelling ?? null };
  writing();
  await call('PUT', `${VINTED_ORIGIN}/api/v2/item_upload/items/${id}`, {
    step: 'update', retry: false, body: { item, push_up: false, parcel: null, upload_session_id: sessionId },
  });
  // Fresh reads decide the outcome, with a short wait for Vinted's read-after-write lag.
  let differ = ['listing'];
  for (const wait of VINTED_LIVE_READ_WAITS_MS) {
    if (wait) {
      if (Date.now() + wait > payload.deadline - 2000) break;
      await marketplaceSleep(wait);
    }
    const view = await vintedRead(call, id);
    differ = !view ? ['listing'] : !vintedLive(view) ? ['live status'] : vintedDifferences(view, vintedExpected(fields, photos.length));
    if (!differ.length) break;
  }
  if (differ.length) {
    throw marketplaceError(`Vinted did not keep this listing's ${differ.join(', ')}. Check the live listing.`,
      { code: 'uncertain', rejected: false, marketplace_message: `read back differs: ${differ.join(', ')}` });
  }
  return { ok: true, state: 'published', listing_url: listingUrl };
}

async function runVintedNative(msg, recovery = false) {
  const payload = { ...(msg.payload || {}), deadline: Date.now() + 120000 };
  const listing = payload.listing && typeof payload.listing === 'object' ? payload.listing : {};
  const key = `native_operation_${payload.operation_id}`;
  const photos = Array.isArray(payload.photos) ? payload.photos.slice(0, VINTED_MAX_PHOTOS) : [];
  let wrote = false;
  let replacedDraft = false;
  const storeReceipt = async receipt => {
    const journal = (await chrome.storage.local.get(key))[key] || {};
    await chrome.storage.local.set({ [key]: { ...journal, ...receipt } });
  };
  const fail = (error, extra = {}) => marketplaceFailure(error, error?.rejected === true && !wrote, {
    ...(payload.draft_url ? { draft_url: payload.draft_url } : {}),
    ...(replacedDraft && payload.draft_url ? { draft_replaced: true } : {}),
    ...extra,
  });
  try {
    if (!['save_draft', 'publish', 'update'].includes(payload.action)) return marketplaceFailure(marketplaceError('Unsupported Vinted action.'), true);
    if (payload.action === 'publish' && !recovery && payload.confirm !== true) {
      return marketplaceFailure(marketplaceError('Confirm listing on Vinted.'), true);
    }
    if (payload.action === 'update' && !recovery && payload.confirm !== true) {
      return marketplaceFailure(marketplaceError('Confirm updating the live Vinted listing.'), true);
    }
    const session = await vintedSession(payload.deadline);
    const call = (method, url, options = {}) => session.call(method, url, { ...options, action: payload.action, confirm: payload.confirm });
    if (payload.action === 'update') {
      return await updateVintedItem(call, session, payload, listing, recovery, () => { wrote = true; });
    }
    const id = vintedDraftId(payload.draft_url);
    if (payload.draft_url && !id) return marketplaceFailure(marketplaceError('Invalid native draft link.'), true);
    if (recovery && payload.action === 'publish') {
      const journal = (await chrome.storage.local.get(key))[key] || {};
      if (journal.listing_url) return { ok: true, state: 'published', draft_url: payload.draft_url, listing_url: journal.listing_url };
      const view = id ? await vintedRead(call, id) : null;
      if (vintedLive(view)) return { ok: true, state: 'published', draft_url: payload.draft_url, listing_url: vintedPublicUrl(view.id) };
      return fail(marketplaceError('Vinted did not confirm the listing. Check your Vinted listings before trying again.', { code: 'uncertain', rejected: false }));
    }
    if (recovery && payload.action === 'save_draft') {
      let view = id ? await vintedRead(call, id) : null;
      if (!view && !id) {
        const expected = { title: marketplaceCopy(listing, 'vinted', 'title'), price: vintedAmount(listing.vinted_specifics?.price ?? listing.price) };
        const matches = (await vintedWardrobe(call, session.userId)).filter(item => item?.is_draft === true && vintedSame(item, expected));
        if (matches.length === 1) {
          payload.draft_url = vintedDraftUrl(matches[0].id);
          view = vintedView(matches[0]);
        }
      }
      if (!view) return fail(marketplaceError('Vinted did not confirm a draft. Check your Vinted drafts before trying again.', { code: 'uncertain', rejected: false }));
      const fields = await vintedFields(call, listing, session.currency);
      const expected = vintedExpected(fields, payload.expected_photo_count ?? photos.length);
      const differ = vintedDifferences(view, expected);
      if (differ.length) {
        return fail(marketplaceError(`Vinted's listing does not match this revision (${differ.join(', ')}). List again.`, { code: 'uncertain', rejected: false }));
      }
      payload.draft_url = vintedDraftUrl(view.id);
      return { ok: true, state: 'draft', draft_url: payload.draft_url };
    }
    if (!photos.length || photos.length > VINTED_MAX_PHOTOS) {
      throw marketplaceMissing('vinted', [{ field: 'photos', needs: `between one and ${VINTED_MAX_PHOTOS} photos` }]);
    }
    const fields = await vintedFields(call, listing, session.currency);
    if (payload.action === 'publish') {
      if (!id) return marketplaceFailure(marketplaceError('Vinted did not confirm a listing to publish. List again.'), true);
      const view = await vintedRead(call, id);
      if (vintedLive(view)) return { ok: true, state: 'published', draft_url: payload.draft_url, listing_url: vintedPublicUrl(view.id) };
      if (!view) {
        throw marketplaceError('Could not find draft on Vinted. List again.', { marketplace_message: 'Could not find draft' });
      }
      const differ = vintedDifferences(view, vintedExpected(fields, photos.length));
      if (view.is_draft === false || differ.length) {
        throw marketplaceError(`Vinted's draft does not match this revision${differ.length ? ` (${differ.join(', ')})` : ''}. List again.`);
      }
      const completionPhotos = (view.photo_ids || []).map(photoId => ({ id: photoId, orientation: 0 }));
      if (completionPhotos.length !== photos.length) {
        throw marketplaceError('Vinted\'s draft does not match this revision (photos). List again.');
      }
      const form = vintedForm(fields, completionPhotos, crypto.randomUUID(), Number(id));
      const { assigned_photos: attached, ...rest } = form;
      wrote = true;
      const completed = await call('POST', `${VINTED_ORIGIN}/api/v2/item_upload/drafts/${id}/completion`, {
        step: 'publish', retry: false, body: {
          draft: { ...rest, photos: attached },
          push_up: false, parcel: null, upload_session_id: form.temp_uuid,
        },
      });
      const publishedId = String(completed?.item?.id || completed?.draft?.id || id);
      if (!/^[1-9]\d{0,14}$/.test(publishedId)) {
        throw marketplaceError('Vinted did not confirm the new listing. Check your Vinted listings before trying again.', { code: 'uncertain', rejected: false });
      }
      const after = await vintedRead(call, publishedId);
      if (!vintedLive(after)) {
        throw marketplaceError('Vinted did not confirm the listing. Check your Vinted listings before trying again.', { code: 'uncertain', rejected: false });
      }
      const listingUrl = vintedPublicUrl(publishedId);
      await storeReceipt({ listing_url: listingUrl });
      return { ok: true, state: 'published', draft_url: payload.draft_url, listing_url: listingUrl };
    }
    const sessionId = crypto.randomUUID();
    const assigned = await vintedUploadPhotos(call, msg.job_id || payload.operation_id, photos, sessionId, payload.deadline);
    const form = vintedForm(fields, assigned, sessionId, id ? Number(id) : null);
    let draftId = id;
    try {
      if (draftId) {
        wrote = true;
        const saved = await call('PUT', `${VINTED_ORIGIN}/api/v2/item_upload/drafts/${draftId}`, {
          step: 'save', retry: false, body: { draft: form, parcel: null, upload_session_id: sessionId },
        });
        draftId = String(saved?.draft?.id || saved?.item?.id || draftId);
      } else {
        wrote = true;
        const saved = await call('POST', `${VINTED_ORIGIN}/api/v2/item_upload/drafts`, {
          step: 'save', retry: false, body: { draft: form, parcel: null, upload_session_id: sessionId },
        });
        draftId = String(saved?.draft?.id || saved?.item?.id || '');
      }
    } catch (error) {
      if (id && error.status === 404) {
        payload.draft_url = '';
        replacedDraft = true;
        wrote = true;
        const saved = await call('POST', `${VINTED_ORIGIN}/api/v2/item_upload/drafts`, {
          step: 'save', retry: false, body: { draft: { ...form, id: null }, parcel: null, upload_session_id: sessionId },
        });
        draftId = String(saved?.draft?.id || saved?.item?.id || '');
      } else throw error;
    }
    if (!/^[1-9]\d{0,14}$/.test(draftId)) {
      throw marketplaceError('Vinted did not confirm its new draft. Check your Vinted drafts before trying again.', { code: 'schema_drift', rejected: false });
    }
    payload.draft_url = vintedDraftUrl(draftId);
    await storeReceipt({ receipt_url: payload.draft_url, expected_photo_count: photos.length });
    let view = null;
    for (let attempt = 0; attempt < 3 && !view; attempt += 1) {
      view = await vintedRead(call, draftId);
      if (!view && attempt < 2) await marketplaceSleep(400);
    }
    if (!view) {
      const matches = (await vintedWardrobe(call, session.userId)).filter(item => String(item?.id) === draftId);
      view = matches.length === 1 ? vintedView(matches[0]) : null;
    }
    const differ = view ? vintedDifferences(view, vintedExpected(fields, photos.length)) : ['draft'];
    if (!view || differ.length) {
      throw marketplaceError(`Vinted did not keep everything this listing sent${differ.length ? ` (${differ.join(', ')})` : ''}. Check the draft and list again.`,
        { code: 'uncertain', rejected: false });
    }
    return { ok: true, state: 'draft', draft_url: payload.draft_url, ...(replacedDraft ? { draft_replaced: true } : {}) };
  } catch (error) {
    return fail(error?.code ? error : marketplaceError(String(error?.message || error), { code: 'rejected' }));
  }
}

// Read-only choices for the editor's brand picker.
async function vintedBrandOptions({ query = '', category = '' }) {
  const { call } = await vintedSession(Date.now() + 20000);
  const params = new URLSearchParams({ keyword: String(query).slice(0, 100) });
  const parts = String(category).split('>').map(part => marketplaceText(part).toLowerCase()).filter(Boolean);
  if (parts.length) {
    const catalogs = await call('GET', `${VINTED_ORIGIN}/api/v2/item_upload/catalogs`, { step: 'category' });
    const id = vintedFindCatalog(catalogs?.catalogs || catalogs, parts);
    if (id) params.set('category_id', String(id));
  }
  const found = await call('GET', `${VINTED_ORIGIN}/api/v2/item_upload/brands?${params}`, { step: 'brand' });
  return [...new Set((found?.brands || []).map(row => row.title).filter(Boolean))];
}
