// Mercari draft saving from the extension worker, with no marketplace tab.
// Operations and their persisted-query hashes come from Mercari's own web
// client (recorded 2026-10-24, hashes rebuilt from its bundle and checked
// against recorded requests). Each operation reads a fresh session from
// Mercari's own /v1/initialize with the seller's cookies; nothing is stored.
const MERCARI_API = 'https://www.mercari.com/v1/api';
const MERCARI_INITIALIZE = 'https://www.mercari.com/v1/initialize';
const MERCARI_SESSION_RETRY_MS = 2000;
const MERCARI_PHOTO_CONCURRENCY = 3;
// This operation's session, as a promise so parallel uploads share one read.
let mercariSession = null;
// Earlier builds kept a captured bearer token; it is no longer used.
chrome.storage.local.remove('mercari_session').catch(() => {});
const MERCARI_OPERATIONS = {
  uploadDraftItemImageMutation: '03efaac0867744458287998448d0d8af22779b8503403a41e4baac0f6e63fa10',
  addDraftItemMutation: '2b7de5aa72261ef11ab70a37cd06eb470bf6c57097d3e9f346f57025c4fc9a97',
  editDraftItemMutation: 'ffa6c1877d5e39fdc8a6dbc0b875cf3612c3733282ace58d2cb08430a7bd6506',
  draftItemQuery: 'b3ba0939427d2b9bdc1d81375ac69db21f9366ee42142bd70e209ca3c65950c7',
  listDraftItemMutation: '4278f248431b9448e423c8466457e2d4b819158cce67c38084d060b211e225a3',
};
const MERCARI_MAX_PHOTOS = 12;
const MERCARI_PHOTO_EDGE = 1200;

function mercariDraftId(draftUrl) {
  return String(draftUrl || '').match(/^https:\/\/www\.mercari\.com\/sell\/draft\/([A-Za-z0-9_-]{1,80})\/?$/)?.[1] || null;
}

// Mercari's form sends prices in cents (whole dollars only).
function mercariPriceCents(price) {
  if (price == null || price === '') return null;
  const dollars = Math.round(Number(price));
  return Number.isFinite(dollars) && dollars > 0 ? dollars * 100 : null;
}

// Mercari item conditions: 1 New (with tags), 2 Like new (without tags,
// unused), 3 Good, 4 Fair, 5 Poor. Worn items never map to Like new.
function mercariConditionId(condition) {
  const text = String(condition || '').trim().toLowerCase();
  if (!text) return null;
  if (/^[1-5]$/.test(text)) return Number(text);
  if (/poor|major flaw/.test(text)) return 5;
  if (/fair/.test(text)) return 4;
  if (/new with tags|nwt/.test(text) && !/without/.test(text)) return 1;
  if (/new without tags|nwot|like new/.test(text)) return 2;
  if (/good|excellent|imperfection|pre-owned|used/.test(text)) return 3;
  if (/^new\b/.test(text)) return 1;
  return null;
}

const MERCARI_SHIPPING_PAYER = { SELLER: 2, BUYER: 1 };
// Mercari's prepaid labels stop at 50 lb.
const MERCARI_MAX_LABEL_OZ = 800;

// The leaf a direct list sends. A stored Mercari category id wins. Otherwise
// the id is the one Vendoo has, and a parent of that tree is still too broad.
function mercariCategory(listing) {
  const direct = String(listing.direct_category_ids?.mercari ?? '');
  if (/^\d{1,6}$/.test(direct)) return { id: Number(direct), parent: false };
  const resolved = listing.form?.overrides?.categoryV2;
  const id = resolved?.id ?? listing.marketplace_category_ids?.mercari ?? listing.mercari_specifics?.categoryId;
  if (!/^\d{1,6}$/.test(String(id ?? ''))) return null;
  return { id: Number(id), parent: resolved?.hasChildren === true };
}

function mercariCategoryId(listing) {
  return mercariCategory(listing)?.id ?? null;
}

// Package weight in whole ounces. Mercari's server fails the draft save
// without it (verified 2026-10-24); its form sends ounces with no unit.
function mercariOunces(pounds, ounces) {
  const total = Math.ceil((Number.isFinite(pounds) ? pounds : 0) * 16 + (Number.isFinite(ounces) ? ounces : 0));
  return total > 0 ? total : null;
}

function mercariWeightOz(listing) {
  return mercariOunces(Number(listing.weight_lb || 0), Number(listing.weight_oz || 0))
    || mercariOunces(Number(listing.form?.overrides?.weight?.pounds || 0), Number(listing.form?.overrides?.weight?.ounces || 0));
}

// Mercari's size id: Vendoo's coded ``<leaf>_Size`` first, else the seller's
// size among the category's own sizes (Studio sends them as form.sizes, in
// Mercari's order). A size that only matches inside a fit the listing does
// not name, or several fits when it names one, is not chosen.
function mercariSize(listing, categoryId) {
  const sizes = listing.form?.sizes;
  if (!Array.isArray(sizes) || !sizes.length) return { none: !Array.isArray(sizes) ? 'unknown' : 'none' };
  const coded = Number(listing.form?.aspects?.[`${categoryId}_Size`]);
  if (coded && sizes.some(size => Number(size.id) === coded)) return { id: coded };
  const wanted = String(listing.mercari_specifics?.size || listing.size || '').trim();
  if (!wanted) return { problem: 'a size' };
  const entries = sizes.map(size => ({ size, keys: sizeEntryKeys([size.name, size.title]) }));
  const found = matchSize(wanted, entries);
  if (!found.length) return { problem: 'a size Mercari offers in this category' };
  const groups = new Set(found.map(entry => entry.size.group));
  if (groups.size > 1 && listingSizeFit(listing)) return { problem: `which of Mercari's ${found.map(entry => entry.size.name).join(' or ')} sizes this is` };
  return { id: Number(found[0].size.id) };
}

// What the seller filled in Anteroom, in Mercari's form fields. Studio's
// form carries Vendoo's codes: condition id, the Ground Advantage class for
// the weight, who pays, and the ships-from ZIP. ``brandId`` is added once
// Mercari's id for the brand is known.
// Needs a live check: Mercari's draft input names for size (sizeId) and
// shipping class (shippingClassIds) follow its own "actions required" fields.
function mercariCommon(listing) {
  const specifics = listing.form?.specifics || {};
  const common = { name: String(listing.title || '').trim(), isAutoPriceDrop: false };
  if (listing.description) common.description = String(listing.description).trim();
  const price = mercariPriceCents(listing.mercari_specifics?.price || listing.price);
  if (price != null) common.price = price;
  const categoryId = mercariCategoryId(listing);
  if (categoryId != null) common.categoryId = categoryId;
  const conditionId = mercariConditionId(listing.form?.overrides?.condition || listing.mercari_specifics?.condition || listing.condition);
  if (conditionId != null) common.conditionId = conditionId;
  if (categoryId != null) {
    const size = mercariSize(listing, categoryId);
    if (size.id) common.sizeId = size.id;
  }
  const zip = [specifics.shipping?.location, listing.form?.ships_from_zip, listing.zipCode].map(value => String(value ?? '').trim())
    .find(value => /^\d{5}$/.test(value));
  const weight = mercariWeightOz(listing);
  const payerId = [MERCARI_SHIPPING_PAYER.BUYER, MERCARI_SHIPPING_PAYER.SELLER].includes(Number(specifics.shipping?.payerId))
    ? Number(specifics.shipping.payerId) : MERCARI_SHIPPING_PAYER.BUYER;
  common.shipping = { payerId, ...(zip ? { zipCode: zip } : {}), dimensions: {}, ...(weight ? { weight: { weight } } : {}) };
  const carrierId = Number(specifics.shipping?.carrierId);
  if (Number.isInteger(carrierId) && carrierId > 0) common.shippingClassIds = [carrierId];
  return common;
}

// Everything Mercari would refuse, named before the first upload.
function mercariMissing(listing, common) {
  const missing = [];
  const category = mercariCategory(listing);
  if (!common.name) missing.push({ field: 'name', needs: 'a title' });
  if (common.name.length > 80) missing.push({ field: 'name', needs: 'a title of 80 characters or fewer' });
  if (!common.description) missing.push({ field: 'description', needs: 'a description' });
  if ((common.description || '').length > 1000) missing.push({ field: 'description', needs: 'a description of 1000 characters or fewer' });
  const dollars = (common.price || 0) / 100;
  if (!(dollars >= 1 && dollars <= 2000)) missing.push({ field: 'price', needs: 'a price from $1 to $2,000' });
  if (!category) missing.push({ field: 'categoryId', needs: 'a Mercari category' });
  else if (category.parent) missing.push({ field: 'categoryId', needs: 'a more specific Mercari category' });
  if (common.conditionId == null) missing.push({ field: 'conditionId', needs: 'a condition' });
  if (category) {
    const size = mercariSize(listing, category.id);
    if (size.problem) missing.push({ field: 'sizeId', needs: size.problem });
  }
  const weight = common.shipping.weight?.weight;
  if (!weight) missing.push({ field: 'weight', needs: 'a package weight' });
  else if (weight > MERCARI_MAX_LABEL_OZ) missing.push({ field: 'weight', needs: 'a package of 50 lb or less for a Mercari label' });
  return missing;
}

// Mercari's id for the listing's brand. Mercari's own brand search is not
// known to Anteroom, so this reads the Mercari brand index Vendoo's form uses,
// with Vendoo's saved sign-in only: no tab is opened, and a brand that cannot
// be found is left for Mercari's own check of the draft before it lists.
// Needs a live check: the index answers [{ value: <Mercari brand id>, label }].
async function mercariBrandId(listing, deadline) {
  const overrides = listing.form?.overrides || {};
  if (overrides.noBrand === true) return null;
  const name = String(overrides.brand || listing.brand || '').trim();
  if (!name) return null;
  if (/^\d{1,9}$/.test(name)) return Number(name);
  try {
    return await marketplaceCatalog(`mercari:brand:${marketplaceNameKey(name)}`, async () => {
      let session = await loadCachedVendooSession();
      if (!session) throw new Error('No saved Vendoo sign-in');
      if (!tokenIsFresh(session)) session = await cacheVendooSession(await refreshVendooToken(session));
      const res = await vendooFetchOnce(`${VENDOO_API_BASE}/api/rest/v1/brand/mercari?search=${encodeURIComponent(name)}&matchValue=true`,
        { token: session.access_token, timeoutMs: Math.max(1000, Math.min(15000, deadline - Date.now())) });
      if (!res.ok) throw new Error(`Vendoo brand index returned ${res.status}`);
      const hits = Array.isArray(res.data) ? res.data : [res.data];
      const hit = hits.find(entry => marketplaceNameKey(entry?.label ?? entry?.name) === marketplaceNameKey(name)) || hits[0];
      const id = Number(hit?.value ?? hit?.id);
      return Number.isInteger(id) && id > 0 ? id : null;
    });
  } catch (error) {
    marketplaceLog('mercari', 'brand', { error_code: 'unresolved' });
    return null;
  }
}

// The session Mercari's web client starts with: a CSRF token, the access
// token for its API, and the signed-in user.
async function mercariInitialize(deadline, step = 'initialize') {
  const { data } = await marketplaceFetch(MERCARI_INITIALIZE, { marketplace: 'mercari', step, deadline, timeoutMs: 15000,
    headers: { Accept: 'application/json' } });
  if (!data.csrf || !data.user?.userId || !data.user?.name) {
    throw marketplaceError('Sign in to Mercari in Chrome, then reconnect it in Anteroom. Listing actions will not open a tab.', { code: 'signed_out' });
  }
  if (data.user.name === 'Inactive user') {
    throw marketplaceError('Mercari reports this account as inactive. Check your Mercari account.', { code: 'rejected', marketplace_message: data.user.name });
  }
  return { csrf: String(data.csrf), accessToken: data.accessToken ? String(data.accessToken) : '', user: data.user };
}

function mercariCurrentSession(deadline, fresh = false) {
  if (fresh || !mercariSession) {
    const pending = mercariInitialize(deadline, fresh ? 'reinitialize' : 'initialize');
    mercariSession = pending;
    pending.catch(() => { if (mercariSession === pending) mercariSession = null; });
  }
  return mercariSession;
}

function mercariHeaders(session) {
  return {
    accept: 'application/json',
    'x-csrf-token': session.csrf,
    ...(session.accessToken ? { authorization: `Bearer ${session.accessToken}` } : {}),
    'x-platform': 'web',
    'x-app-version': '1',
    'x-double-web': '1',
    'x-gql-migration': '1',
    'apollo-require-preflight': 'true',
  };
}

// Mercari's GraphQL errors, classified. Its messages pass through as its own words.
function mercariFailure(operationName, errors, write) {
  const list = Array.isArray(errors) ? errors : [];
  const text = JSON.stringify(list).slice(0, 4000);
  const own = list.map(entry => String(entry?.message || '')).filter(Boolean).join(' ');
  if (/PersistedQueryNotFound|PERSISTED_QUERY|is not defined by type|Unknown field|GRAPHQL_VALIDATION_FAILED/i.test(text)) {
    return marketplaceError('Mercari changed its listing form. Anteroom needs an update before listing there.', { code: 'schema_drift', marketplace_message: own });
  }
  if (/unauthenticated|unauthori[sz]ed/i.test(text)) {
    return marketplaceError('Mercari asked to sign in again. Reconnect Mercari in Anteroom.', { code: 'signed_out', marketplace_message: own });
  }
  if (/captcha|verify you|challenge/i.test(text)) {
    return marketplaceError('Mercari asked for a browser check. Open Mercari in Chrome, finish the check, then try again.', { code: 'challenge', marketplace_message: own });
  }
  if (/too many|rate limit/i.test(text)) {
    return marketplaceError('Mercari is limiting requests right now. Wait a few minutes, then try again.', { code: 'rate_limited', marketplace_message: own });
  }
  if (list.some(entry => entry?.message === 'Unable to contact API')) {
    // Mercari's edge could not reach its service; a write may still have landed.
    return marketplaceError('Mercari could not reach its own service.', { code: write ? 'uncertain' : 'network', rejected: !write, marketplace_message: own });
  }
  const label = { addDraftItemMutation: 'the draft', editDraftItemMutation: 'the draft update', listDraftItemMutation: 'the listing',
    uploadDraftItemImageMutation: 'a photo', UpdateItemStatusMutation: 'the delete' }[operationName] || 'the request';
  return marketplaceError(`Mercari refused ${label}.`, { code: 'rejected', marketplace_message: own || 'Mercari returned no data.' });
}

// One GraphQL request. A rejected session ("Unauthorized", HTTP 401/403) is
// read again from /v1/initialize after a short wait and the request sent again,
// at most twice: Mercari refused it before doing anything. A safe request is
// also sent again when Mercari could not reach its own API.
async function mercariRequest(body, { multipart = null, deadline, operationName = body?.operationName, retry = false }) {
  const write = !/Query$/.test(operationName);
  for (let attempt = 0; ; attempt += 1) {
    const headers = mercariHeaders(await mercariCurrentSession(deadline));
    if (!multipart) headers['content-type'] = 'application/json';
    let failure;
    try {
      const { data } = await marketplaceFetch(MERCARI_API, { marketplace: 'mercari', step: operationName, method: 'POST', headers,
        body: multipart || JSON.stringify(body), deadline, timeoutMs: multipart ? 45000 : 25000, retry });
      if (!data.errors?.length && data.data) return data.data;
      failure = mercariFailure(operationName, data.errors, write && !retry);
    } catch (error) {
      failure = error;
    }
    const sessionRefused = failure.code === 'signed_out';
    const unreachable = retry && failure.code === 'network' && failure.marketplace_message === 'Unable to contact API';
    if (attempt >= 2 || !(sessionRefused || unreachable) || Date.now() + MERCARI_SESSION_RETRY_MS + 1000 >= deadline) throw failure;
    marketplaceLog('mercari', operationName, { retry: attempt + 1, error_code: failure.code });
    await marketplaceSleep(MERCARI_SESSION_RETRY_MS);
    await mercariCurrentSession(deadline, true);
  }
}

function mercariOperation(operationName, variables) {
  return { operationName, variables, extensions: { persistedQuery: { version: 1, sha256Hash: MERCARI_OPERATIONS[operationName] } } };
}

async function mercariPhotoBlob(photo) {
  const bytes = Uint8Array.from(atob(photo.base64), char => char.charCodeAt(0));
  const image = await createImageBitmap(new Blob([bytes], { type: photo.mime_type }));
  const scale = Math.min(1, MERCARI_PHOTO_EDGE / Math.max(image.width, image.height));
  const canvas = new OffscreenCanvas(Math.round(image.width * scale), Math.round(image.height * scale));
  const context = canvas.getContext('2d');
  context.fillStyle = '#ffffff';
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  image.close();
  return canvas.convertToBlob({ type: 'image/jpeg', quality: 0.92 });
}

// GraphQL multipart request: one "operations" part, a "map", then the file.
// One photo per request, a few at a time, in the seller's order. An upload
// only stores an image for a later draft save, so it is safe to repeat.
async function mercariUploadPhoto(jobId, photo, deadline) {
  const blob = await mercariPhotoBlob(await nativePhoto(jobId, photo, deadline));
  const form = new FormData();
  form.append('operations', JSON.stringify(mercariOperation('uploadDraftItemImageMutation', { input: { photos: [null] } })));
  form.append('map', JSON.stringify({ 0: ['variables.input.photos.0'] }));
  form.append('0', blob, '0_photo.jpg');
  const data = await mercariRequest(null, { multipart: form, deadline, operationName: 'uploadDraftItemImageMutation', retry: true });
  const id = String(data.uploadDraftItemImage?.images?.[0]?.id || '');
  if (!/^[A-Za-z0-9_-]{1,120}$/.test(id)) throw marketplaceError('Mercari did not confirm a photo upload.', { code: 'schema_drift' });
  return id;
}

async function mercariUploadPhotos(jobId, photos, deadline) {
  const ids = new Array(photos.length);
  let next = 0;
  let failed = false;
  const worker = async () => {
    while (!failed && next < photos.length) {
      const index = next++;
      try { ids[index] = await mercariUploadPhoto(jobId, photos[index], deadline); }
      catch (error) { failed = true; throw error; }
    }
  };
  await Promise.all(Array.from({ length: Math.min(MERCARI_PHOTO_CONCURRENCY, photos.length) }, worker));
  return ids;
}

async function readMercariDraft(id, deadline) {
  const data = await mercariRequest(mercariOperation('draftItemQuery', { input: { draftItemId: id } }), { deadline, retry: true });
  const draft = data.draftItem;
  if (!draft) throw marketplaceError('Mercari did not return the saved draft.', { code: 'schema_drift' });
  return { name: draft.name, description: draft.description, price: draft.price,
    conditionId: draft.itemCondition?.id ?? null, photo_count: draft.photos?.length || 0,
    status: draft.draftItemStatus || null,
    actions_required: (draft.draftItemActionsRequired || []).map(action => String(action.field || '')).filter(Boolean) };
}

function mercariDraftMatches(draft, listing, expectedPhotos) {
  const common = mercariCommon(listing);
  return marketplaceText(draft.name) === marketplaceText(common.name)
    && (common.description == null || marketplaceText(draft.description) === marketplaceText(common.description))
    && (common.price == null || Number(draft.price) === common.price)
    && (common.conditionId == null || draft.conditionId == null || Number(draft.conditionId) === common.conditionId)
    && (expectedPhotos == null || draft.photo_count === expectedPhotos);
}

async function runMercariNative(msg, recovery = false) {
  const payload = { ...msg.payload, deadline: Date.now() + 120000 };
  const key = `native_operation_${payload.operation_id}`;
  const storeReceipt = async receipt => {
    const journal = (await chrome.storage.local.get(key))[key] || {};
    await chrome.storage.local.set({ [key]: { ...journal, ...receipt } });
  };
  mercariSession = null;
  // prepare -> writing (the draft mutation is in flight) -> written
  let stage = 'prepare';
  try {
    if (payload.action === 'publish') return await publishMercariDraft(payload, storeReceipt, recovery);
    if (payload.action !== 'save_draft') return null;
    let id = mercariDraftId(payload.draft_url);
    if (recovery) {
      if (!id) return marketplaceFailure(new Error('Mercari did not confirm a draft. Check your Mercari drafts before trying again.'), false);
      const draft = await readMercariDraft(id, payload.deadline);
      if (!mercariDraftMatches(draft, payload.listing, payload.expected_photo_count)) {
        return marketplaceFailure(new Error('Mercari\'s listing does not match this revision. List again.'), false, { draft_url: payload.draft_url });
      }
      return { ok: true, state: 'draft', draft_url: payload.draft_url };
    }
    const common = mercariCommon(payload.listing);
    const missing = mercariMissing(payload.listing, common);
    if (missing.length) throw marketplaceMissing('mercari', missing);
    const brandId = await mercariBrandId(payload.listing, payload.deadline);
    if (brandId) common.brandId = brandId;
    let expectedPhotos = null;
    if (id) {
      stage = 'writing';
      const data = await mercariRequest(mercariOperation('editDraftItemMutation', { input: { id, common } }), { deadline: payload.deadline });
      stage = 'written';
      if (String(data.editDraftItem?.id || '') !== id) throw new Error('Mercari did not confirm the draft update. Check the draft before trying again.');
    } else {
      const photos = payload.photos || [];
      if (!photos.length || photos.length > MERCARI_MAX_PHOTOS) {
        throw marketplaceMissing('mercari', [{ field: 'photos', needs: `between one and ${MERCARI_MAX_PHOTOS} photos` }]);
      }
      const imageIds = await mercariUploadPhotos(msg.job_id, photos, payload.deadline);
      stage = 'writing';
      const data = await mercariRequest(mercariOperation('addDraftItemMutation', { input: { common: { ...common, imageIds } } }), { deadline: payload.deadline });
      stage = 'written';
      id = String(data.addDraftItem?.item?.id || '');
      if (!/^[A-Za-z0-9_-]{1,80}$/.test(id)) throw new Error('Mercari did not confirm a new draft. Check your Mercari drafts before trying again.');
      payload.draft_url = `https://www.mercari.com/sell/draft/${id}/`;
      expectedPhotos = photos.length;
      await storeReceipt({ receipt_url: payload.draft_url, expected_photo_count: expectedPhotos });
    }
    const draft = await readMercariDraft(id, payload.deadline);
    if (!mercariDraftMatches(draft, payload.listing, expectedPhotos)) {
      throw new Error('Mercari did not keep this listing\'s fields and photos. Check them and list again.');
    }
    return { ok: true, state: 'draft', draft_url: payload.draft_url };
  } catch (error) {
    // A draft mutation Mercari clearly refused wrote nothing.
    const safe = !recovery && (stage === 'prepare' || (stage === 'writing' && error.rejected === true));
    return marketplaceFailure(error, safe, { draft_url: payload.draft_url || null });
  }
}

const MERCARI_ACTION_LABELS = { categoryId: 'a category', conditionId: 'a condition', brandId: 'a brand', sizeId: 'a size',
  shipping: 'shipping', shippingClassIds: 'shipping', photos: 'photos', price: 'a price', name: 'a title', description: 'a description' };

// Publishing lists an existing READY draft the way Mercari's own drafts page
// does: one listDraftItemMutation with the draft ID. Seller-confirmed only,
// sent at most once, and never retried when the reply is lost.
async function publishMercariDraft(payload, storeReceipt, recovery) {
  const id = mercariDraftId(payload.draft_url);
  if (!id) return marketplaceFailure(marketplaceError('Mercari did not confirm a listing to publish. List again.'), true);
  if (recovery) {
    const journal = (await chrome.storage.local.get(`native_operation_${payload.operation_id}`))[`native_operation_${payload.operation_id}`] || {};
    if (journal.listing_url) return { ok: true, state: 'published', draft_url: payload.draft_url, listing_url: journal.listing_url };
    return marketplaceFailure(new Error('Mercari did not confirm the listing. Check your Mercari listings before trying again.'), false, { draft_url: payload.draft_url });
  }
  if (payload.confirm !== true) return marketplaceFailure(marketplaceError('Confirm publishing on Mercari.'), true);
  let stage = 'prepare';
  try {
    const draft = await readMercariDraft(id, payload.deadline);
    if (!mercariDraftMatches(draft, payload.listing, null)) {
      throw marketplaceError('Mercari\'s listing does not match this revision. List again.');
    }
    if (draft.status !== 'DRAFT_ITEM_STATUS_READY') {
      const fields = [...new Set(draft.actions_required)];
      throw marketplaceMissing('mercari', (fields.length ? fields : ['details']).map(field => ({ field,
        needs: MERCARI_ACTION_LABELS[field] || String(field).replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase() })),
      fields.length ? `draftItemActionsRequired: ${fields.join(', ')}` : String(draft.status || ''));
    }
    stage = 'publishing';
    const data = await mercariRequest(mercariOperation('listDraftItemMutation', { input: { draftItemId: id } }), { deadline: payload.deadline });
    stage = 'published';
    const itemId = String(data.listDraftItem || '');
    if (!/^m\d{6,}$/.test(itemId)) throw new Error('Mercari did not confirm the new listing. Check your Mercari listings before trying again.');
    const listingUrl = `https://www.mercari.com/us/item/${itemId}/`;
    await storeReceipt({ listing_url: listingUrl });
    return { ok: true, state: 'published', draft_url: payload.draft_url, listing_url: listingUrl };
  } catch (error) {
    const safe = stage === 'prepare' || (stage === 'publishing' && error.rejected === true);
    return marketplaceFailure(error, safe, { draft_url: payload.draft_url });
  }
}
