// Mercari draft saving from the extension worker, with no marketplace tab.
// Operations and their persisted-query hashes come from Mercari's own web
// client (recorded 2026-10-24, hashes rebuilt from its bundle and checked
// against recorded requests). Session headers were captured during an explicit
// reconnect and stay in this worker's session storage.
const MERCARI_API = 'https://www.mercari.com/v1/api';
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
  const text = String(condition || '').toLowerCase();
  if (!text) return null;
  if (/poor|major flaw/.test(text)) return 5;
  if (/fair/.test(text)) return 4;
  if (/new with tags|nwt/.test(text) && !/without/.test(text)) return 1;
  if (/new without tags|nwot|like new/.test(text)) return 2;
  if (/good|excellent|imperfection|pre-owned|used/.test(text)) return 3;
  if (/^new\b/.test(text)) return 1;
  return null;
}

const MERCARI_SHIPPING_PAYER = { SELLER: 2, BUYER: 1 };

function mercariCategoryId(listing) {
  const id = listing.marketplace_category_ids?.mercari ?? listing.mercari_specifics?.categoryId;
  return /^\d{1,6}$/.test(String(id ?? '')) ? Number(id) : null;
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

// What the seller filled in Anteroom, in Mercari's form fields. Brand, size
// and shipping class are optional for a draft and are chosen on Mercari.
function mercariCommon(listing) {
  const common = { name: String(listing.title || '').slice(0, 80), isAutoPriceDrop: false };
  if (listing.description) common.description = String(listing.description).slice(0, 1000);
  const price = mercariPriceCents(listing.price);
  if (price != null) common.price = price;
  const categoryId = mercariCategoryId(listing);
  if (categoryId != null) common.categoryId = categoryId;
  const conditionId = mercariConditionId(listing.mercari_specifics?.condition || listing.condition);
  if (conditionId != null) common.conditionId = conditionId;
  const zipCode = /^\d{5}$/.test(String(listing.zipCode || '')) ? String(listing.zipCode) : null;
  const weight = mercariWeightOz(listing);
  common.shipping = { payerId: MERCARI_SHIPPING_PAYER.BUYER, ...(zipCode ? { zipCode } : {}), dimensions: {},
    ...(weight ? { weight: { weight } } : {}) };
  return common;
}

async function mercariHeaders() {
  const stored = await mercariStoredSession();
  const captured = stored?.headers || {};
  const exp = jwtClaims(String(captured.authorization || '').replace(/^Bearer\s+/i, ''))?.exp;
  if (!captured.authorization || !captured['x-csrf-token'] || (Number.isFinite(exp) && exp * 1000 <= Date.now())) {
    throw new Error('Reconnect Mercari in Anteroom first. Listing actions will not open a tab.');
  }
  return {
    authorization: captured.authorization,
    'x-csrf-token': captured['x-csrf-token'],
    'x-platform': captured['x-platform'] || 'web',
    ...(captured['x-app-version'] ? { 'x-app-version': captured['x-app-version'] } : {}),
    // Feature-flag variants only. Anti-fraud and device signals are never copied.
    ...(captured['x-ld-variants'] ? { 'x-ld-variants': captured['x-ld-variants'] } : {}),
    'x-double-web': '1',
    'apollo-require-preflight': 'true',
  };
}

// Mercari's GraphQL errors carry a code and a short user-facing message
// (for example a field it requires). Keep those, capped, and nothing else.
function mercariFailure(operationName, status, errors) {
  const text = JSON.stringify(errors || '').slice(0, 4000);
  if (status === 401 || /unauthenticated|unauthori[sz]ed/i.test(text)) return 'Mercari asked to sign in again. Reconnect Mercari in Anteroom.';
  if (/PersistedQueryNotFound|PERSISTED_QUERY/i.test(text)) return 'Mercari changed its listing form. Anteroom needs an update before listing there.';
  if (/captcha|verify you|challenge/i.test(text)) return 'Mercari requested browser verification.';
  const first = Array.isArray(errors) ? errors[0] : null;
  const code = String(first?.extensions?.code || first?.extensions?.errorCode || '').replace(/[^A-Za-z0-9_.-]/g, '').slice(0, 60);
  const message = String(first?.message || '').replace(/[\u0000-\u001f]/g, ' ').slice(0, 160);
  return `Mercari rejected ${operationName}${status && status !== 200 ? ` (HTTP ${status})` : ''}${code ? ` [${code}]` : ''}${message ? `: ${message}` : '.'}`;
}

async function mercariRequest(body, { multipart = null, deadline, operationName = body?.operationName }) {
  if (Date.now() >= deadline || activeNativeOperation?.cancelled) {
    throw new Error('The Mercari operation stopped. Check its result before trying again.');
  }
  await ensureMarketplaceRequestContext();
  const headers = await mercariHeaders();
  const init = { method: 'POST', credentials: 'include', signal: AbortSignal.timeout(30000), headers };
  if (multipart) init.body = multipart;
  else { headers['content-type'] = 'application/json'; init.body = JSON.stringify(body); }
  const response = await fetch(MERCARI_API, init);
  let result = null;
  try { result = await response.json(); } catch {}
  if (!response.ok || result?.errors?.length || !result?.data) throw new Error(mercariFailure(operationName, response.status, result?.errors));
  return result.data;
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

// GraphQL multipart request: one "operations" part, a "map", then the files.
async function mercariUploadPhotos(jobId, photos, deadline) {
  const blobs = [];
  for (const photo of photos) blobs.push(await mercariPhotoBlob(await nativePhoto(jobId, photo)));
  const form = new FormData();
  form.append('operations', JSON.stringify(mercariOperation('uploadDraftItemImageMutation', { input: { photos: blobs.map(() => null) } })));
  form.append('map', JSON.stringify(Object.fromEntries(blobs.map((_, index) => [String(index), [`variables.input.photos.${index}`]]))));
  blobs.forEach((blob, index) => form.append(String(index), blob, `${index}_photo.jpg`));
  const data = await mercariRequest(null, { multipart: form, deadline, operationName: 'uploadDraftItemImageMutation' });
  const ids = (data.uploadDraftItemImage?.images || []).map(image => String(image.id));
  if (ids.length !== photos.length || ids.some(id => !/^[A-Za-z0-9_-]{1,120}$/.test(id))) {
    throw new Error('Mercari did not confirm every photo upload.');
  }
  return ids;
}

async function readMercariDraft(id, deadline) {
  const data = await mercariRequest(mercariOperation('draftItemQuery', { input: { draftItemId: id } }), { deadline });
  const draft = data.draftItem;
  if (!draft) throw new Error('Mercari did not return the saved draft.');
  return { name: draft.name, description: draft.description, price: draft.price,
    conditionId: draft.itemCondition?.id ?? null, photo_count: draft.photos?.length || 0,
    status: draft.draftItemStatus || null,
    actions_required: (draft.draftItemActionsRequired || []).map(action => String(action.field || '')).filter(Boolean) };
}

function mercariDraftMatches(draft, listing, expectedPhotos) {
  const common = mercariCommon(listing);
  return draft.name === common.name
    && (common.description == null || draft.description === common.description)
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
  let committed = recovery;
  try {
    if (payload.action === 'publish') return await publishMercariDraft(payload, storeReceipt, recovery);
    if (payload.action !== 'save_draft') return null;
    let id = mercariDraftId(payload.draft_url);
    if (recovery) {
      if (!id) return { ok: false, error: 'Mercari did not confirm a draft. Check your Mercari drafts before trying again.' };
      const draft = await readMercariDraft(id, payload.deadline);
      if (!mercariDraftMatches(draft, payload.listing, payload.expected_photo_count)) {
        return { ok: false, draft_url: payload.draft_url, error: 'Mercari\'s listing does not match this revision. List again.' };
      }
      return { ok: true, state: 'draft', draft_url: payload.draft_url };
    }
    const common = mercariCommon(payload.listing);
    if (!common.name) throw new Error('Add a title before listing on Mercari.');
    if (!common.shipping.weight) throw new Error('Add a package weight before listing on Mercari.');
    let expectedPhotos = null;
    if (id) {
      committed = true;
      const data = await mercariRequest(mercariOperation('editDraftItemMutation', { input: { id, common } }), { deadline: payload.deadline });
      if (String(data.editDraftItem?.id || '') !== id) throw new Error('Mercari did not confirm the draft update. Check the draft before trying again.');
    } else {
      const photos = payload.photos || [];
      if (!photos.length || photos.length > MERCARI_MAX_PHOTOS) throw new Error(`Choose between one and ${MERCARI_MAX_PHOTOS} Mercari photos.`);
      const imageIds = await mercariUploadPhotos(msg.job_id, photos, payload.deadline);
      committed = true;
      const data = await mercariRequest(mercariOperation('addDraftItemMutation', { input: { common: { ...common, imageIds } } }), { deadline: payload.deadline });
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
    return { ok: false, safe_to_retry: !committed, draft_url: payload.draft_url || null, error: String(error.message || error) };
  }
}

const MERCARI_ACTION_LABELS = { categoryId: 'category', conditionId: 'condition', brandId: 'brand', sizeId: 'size',
  shipping: 'shipping', shippingClassIds: 'shipping', photos: 'photos', price: 'price', name: 'title', description: 'description' };

// Publishing lists an existing READY draft the way Mercari's own drafts page
// does: one listDraftItemMutation with the draft ID. Seller-confirmed only,
// sent at most once, and never retried when the reply is lost.
async function publishMercariDraft(payload, storeReceipt, recovery) {
  const id = mercariDraftId(payload.draft_url);
  if (!id) return { ok: false, safe_to_retry: true, error: 'Mercari did not confirm a listing to publish. List again.' };
  if (recovery) {
    const journal = (await chrome.storage.local.get(`native_operation_${payload.operation_id}`))[`native_operation_${payload.operation_id}`] || {};
    if (journal.listing_url) return { ok: true, state: 'published', draft_url: payload.draft_url, listing_url: journal.listing_url };
    return { ok: false, draft_url: payload.draft_url, error: 'Mercari did not confirm the listing. Check your Mercari listings before trying again.' };
  }
  if (payload.confirm !== true) return { ok: false, safe_to_retry: true, error: 'Confirm publishing on Mercari.' };
  let submitted = false;
  try {
    const draft = await readMercariDraft(id, payload.deadline);
    if (!mercariDraftMatches(draft, payload.listing, null)) {
      return { ok: false, safe_to_retry: true, draft_url: payload.draft_url, error: 'Mercari\'s listing does not match this revision. List again.' };
    }
    if (draft.status !== 'DRAFT_ITEM_STATUS_READY') {
      const needed = [...new Set(draft.actions_required.map(field => MERCARI_ACTION_LABELS[field] || field))];
      return { ok: false, safe_to_retry: true, draft_url: payload.draft_url,
        error: `Mercari needs ${needed.length ? needed.join(', ') : 'more details'} before it can be listed.` };
    }
    submitted = true;
    const data = await mercariRequest(mercariOperation('listDraftItemMutation', { input: { draftItemId: id } }), { deadline: payload.deadline });
    const itemId = String(data.listDraftItem || '');
    if (!/^m\d{6,}$/.test(itemId)) throw new Error('Mercari did not confirm the new listing. Check your Mercari listings before trying again.');
    const listingUrl = `https://www.mercari.com/us/item/${itemId}/`;
    await storeReceipt({ listing_url: listingUrl });
    return { ok: true, state: 'published', draft_url: payload.draft_url, listing_url: listingUrl };
  } catch (error) {
    return { ok: false, safe_to_retry: !submitted, draft_url: payload.draft_url, error: String(error.message || error) };
  }
}
