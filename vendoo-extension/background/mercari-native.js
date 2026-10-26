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

// Mercari's masterQuery fields, read from its web client (2026-10-26).
// This public catalog contains no seller information. Never use Vendoo's
// mirrored categories, sizes, or brand ids to validate a direct listing.
const MERCARI_MASTER_QUERY = `query masterQuery {
  master {
    itemCategories { id name parentId itemSizeGroupId displayOrder }
    itemSizeGroups { id title category sizeIds subGroupIds }
    itemSizeSubGroups { id title sizeIds }
    itemSizes { id title subtitle itemSizeGroupId itemSizeSubGroupId displayOrder }
    itemBrands { id name }
  }
}`;
const MERCARI_SHIPPING_QUERY = `query availableShippingClassesV2($input: AvailableShippingClassesInput!) {
  availableShippingClassesV2(input: $input) {
    shippingClasses { id name minWeight maxWeight fee carrier carrierDisplayName requiresDimensions packageSize handlingType rateType }
  }
}`;

function mercariCatalogData(deadline) {
  return marketplaceCatalog('mercari:master', async () => {
    const data = await mercariRequest({ operationName: 'masterQuery', variables: {}, query: MERCARI_MASTER_QUERY }, { deadline, retry: true });
    const master = data.master;
    if (!master || !['itemCategories', 'itemSizeGroups', 'itemSizeSubGroups', 'itemSizes', 'itemBrands']
      .every(key => Array.isArray(master[key])) || !master.itemCategories.length) {
      throw marketplaceError('Mercari did not return its category, size and brand catalog.', { code: 'schema_drift' });
    }
    return master;
  });
}

function mercariCatalogCategories(master) {
  const categories = new Map(master.itemCategories.map(row => [Number(row.id), row]));
  const parents = new Set(master.itemCategories.map(row => Number(row.parentId)));
  return master.itemCategories.map(row => {
    const names = [], seen = new Set();
    let current = row;
    while (current && !seen.has(current.id)) {
      seen.add(current.id);
      names.unshift(current.name);
      current = categories.get(Number(current.parentId));
    }
    return { label: names.join(' > '), value: row.id, parent: parents.has(Number(row.id)) };
  });
}

function mercariCatalogListing(listing, master) {
  const id = mercariCategoryId(listing);
  const category = master.itemCategories.find(row => Number(row.id) === id);
  if (!category) throw marketplaceMissing('mercari', [{ field: 'categoryId', needs: 'a category from Mercari’s current list' }]);
  const children = master.itemCategories.filter(row => Number(row.parentId) === id);
  const group = master.itemSizeGroups.find(row => Number(row.id) === Number(category.itemSizeGroupId));
  if (category.itemSizeGroupId && !group) throw marketplaceError('Mercari’s size group is missing. Refresh its catalog.', { code: 'schema_drift' });
  const byId = new Map(master.itemSizes.map(row => [Number(row.id), row]));
  const subgroups = new Map(master.itemSizeSubGroups.map(row => [Number(row.id), row]));
  const sizes = (group?.sizeIds || []).map(sizeId => {
    const row = byId.get(Number(sizeId));
    if (!row) throw marketplaceError('Mercari’s size list is incomplete. Refresh its catalog.', { code: 'schema_drift' });
    const label = [row.title, row.subtitle].filter(Boolean).join(' ');
    return { id: row.id, name: label, title: label, group: row.itemSizeSubGroupId,
      group_name: subgroups.get(Number(row.itemSizeSubGroupId))?.title || '' };
  });
  return { ...listing, form: { ...listing.form, sizes, overrides: { ...listing.form?.overrides,
    categoryV2: { id, hasChildren: children.length > 0, children: children.map(row => row.name),
      displayPath: mercariCatalogCategories(master).find(row => Number(row.value) === id)?.label.split(' > ') || [] },
  } } };
}

async function mercariShippingClasses(listing, deadline) {
  const weight = mercariWeightOz(listing), dimensions = mercariDimensions(listing);
  if (!weight || !mercariCategoryId(listing)) return [];
  const { length = 0, width = 0, height = 0 } = dimensions;
  const data = await mercariRequest({ operationName: 'availableShippingClassesV2', query: MERCARI_SHIPPING_QUERY,
    variables: { input: { categoryId: mercariCategoryId(listing), packageSize: length * width * height,
      dimension: { length, width, height }, packageWeight: weight } } }, { deadline, retry: true });
  const rows = data.availableShippingClassesV2?.shippingClasses;
  if (!Array.isArray(rows)) throw marketplaceError('Mercari did not return its shipping labels.', { code: 'schema_drift' });
  return rows.filter(row => Number(row.id) > 0 && Number(row.maxWeight) >= weight
    && (!row.requiresDimensions || length > 0 && width > 0 && height > 0));
}

function mercariShippingListing(listing, classes) {
  const selected = listing.mercari_specifics?.shippingClassId;
  const preferred = classes.find(row => String(row.id) === String(selected));
  const automatic = classes.find(row => row.carrierDisplayName === 'USPS Ground Advantage');
  const label = selected != null && selected !== '' ? preferred : automatic;
  return { ...listing, form: { ...listing.form, shipping_classes: classes,
    specifics: { ...listing.form?.specifics, shipping: { ...listing.form?.specifics?.shipping,
      carrierId: label?.id ?? null } } } };
}

async function mercariPreparedListing(listing, deadline) {
  const prepared = mercariCatalogListing(listing, await mercariCatalogData(deadline));
  return mercariShippingListing(prepared, await mercariShippingClasses(prepared, deadline));
}

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
// the id is the one Vendoo has. A category with subcategories is too broad
// even when Vendoo's tree calls it a leaf: Mercari then lists it with no size.
function mercariCategory(listing) {
  const resolved = listing.form?.overrides?.categoryV2;
  const direct = String(listing.direct_category_ids?.mercari ?? '');
  const id = /^\d{1,6}$/.test(direct) ? direct
    : resolved?.id ?? listing.marketplace_category_ids?.mercari ?? listing.mercari_specifics?.categoryId;
  if (!/^\d{1,6}$/.test(String(id ?? ''))) return null;
  return { id: Number(id), parent: resolved?.hasChildren === true && String(resolved.id) === String(id) };
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

// Mercari sizes whose own numbers hold one of the seller's: kids' "XL (18/20)"
// is Mercari's "2XL (18)", not the "XL (14-16)" a bare XL would pick.
function mercariNumberedSizes(wanted, sizes) {
  const inner = /\(([^)]*)\)/.exec(wanted)?.[1];
  const numbers = (inner || '').split(/[/,]|\s-\s|-/).map(part => part.trim()).filter(part => /^\d+(?:\.\d+)?$/.test(part)).map(Number);
  if (!numbers.length) return [];
  return sizes.filter(size => {
    const range = /\((\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?\)\s*$/.exec(String(size.name || ''));
    if (!range) return false;
    const low = Number(range[1]);
    const high = Number(range[2] ?? range[1]);
    return numbers.some(number => number >= low && number <= high);
  });
}

// Mercari's size id: the seller's label among the category's own sizes first
// (Studio sends them as form.sizes, in
// Mercari's order), its numbers before its letters. A size that only matches
// inside a fit the listing does not name, or several fits when it names one,
// is not chosen. ``none: 'unknown'`` means Studio could not read the sizes.
function mercariSize(listing, categoryId) {
  const sizes = listing.form?.sizes;
  if (!Array.isArray(sizes) || !sizes.length) return { none: !Array.isArray(sizes) ? 'unknown' : 'none' };
  if (Object.hasOwn(listing.mercari_specifics || {}, 'nativeSizeId')) {
    const id = Number(listing.mercari_specifics.nativeSizeId);
    return sizes.some(size => Number(size.id) === id) ? { id } : { problem: 'a size Mercari offers in this category' };
  }
  const wanted = mercariWantedSize(listing);
  if (!wanted) {
    const coded = Number(listing.form?.aspects?.[`${categoryId}_Size`]);
    return coded && sizes.some(size => Number(size.id) === coded) ? { id: coded } : { problem: 'a size' };
  }
  const entries = sizes.map(size => ({ size, keys: sizeEntryKeys([size.name, size.title]) }));
  const numbered = mercariNumberedSizes(wanted, sizes);
  const found = numbered.length ? entries.filter(entry => numbered.includes(entry.size)) : matchSize(wanted, entries);
  if (!found.length) return { problem: 'a size Mercari offers in this category' };
  const groups = new Set(found.map(entry => entry.size.group));
  if (groups.size > 1 && listingSizeFit(listing)) return { problem: `which of Mercari's ${found.map(entry => entry.size.name).join(' or ')} sizes this is` };
  return { id: Number(found[0].size.id) };
}

function mercariWantedSize(listing) {
  return String(listing.mercari_specifics?.size || listing.size || '').trim();
}

// Package dimensions in whole inches, from the form or "13x10x3".
function mercariDimensions(listing) {
  const form = listing.form?.overrides?.dimensions || {};
  let sides = [form.length, form.width, form.height];
  if (!sides.every(side => Number(side) > 0)) {
    sides = String(listing.package_dimensions_in || '').split(/\s*[x×*]\s*/i);
  }
  sides = sides.map(Number);
  if (sides.length !== 3 || !sides.every(side => Number.isFinite(side) && side > 0)) return {};
  const [length, width, height] = sides;
  return { unit: 'INCH', length, width, height };
}

// What the seller filled in Anteroom, in Mercari's form fields. Studio's
// form carries Vendoo's codes: condition id, the Ground Advantage class for
// the weight, who pays, and the ships-from ZIP. ``brandId`` is added once
// Mercari's id for the brand is known. The shipping class goes inside
// ``shipping`` as ``classIds`` and ``suggestedClassIds``, the way Mercari's
// own draft form sends it (recorded 2026-10-24): without it Mercari saves
// the draft as ship-on-your-own and makes the seller pay.
function mercariCommon(listing) {
  const specifics = listing.form?.specifics || {};
  const smart = listing.mercari_specifics?.smartPricing;
  const common = { name: String(listing.title || '').trim(), isAutoPriceDrop: smart?.enabled === true };
  if (common.isAutoPriceDrop) common.minPriceForAutoPriceDrop = mercariPriceCents(smart.minimumPrice);
  if (listing.mercari_specifics?.noBrand === true || Object.hasOwn(listing, 'brand') && !String(listing.brand || '').trim()) common.brandId = null;
  if (listing.description) common.description = String(listing.description).trim();
  const price = mercariPriceCents(listing.mercari_specifics?.price || listing.price);
  if (price != null) common.price = price;
  const categoryId = mercariCategoryId(listing);
  if (categoryId != null) common.categoryId = categoryId;
  const conditionId = mercariConditionId(listing.mercari_specifics?.condition || listing.form?.overrides?.condition || listing.condition);
  if (conditionId != null) common.conditionId = conditionId;
  if (categoryId != null) {
    const size = mercariSize(listing, categoryId);
    if (size.id) common.sizeId = size.id;
  }
  const zip = [listing.form?.ships_from_zip, listing.zipCode, specifics.shipping?.location].map(value => String(value ?? '').trim())
    .find(value => /^\d{5}$/.test(value));
  const weight = mercariWeightOz(listing);
  const chosenPayer = listing.mercari_specifics?.shippingPayerId ?? specifics.shipping?.payerId;
  const payerId = [MERCARI_SHIPPING_PAYER.BUYER, MERCARI_SHIPPING_PAYER.SELLER].includes(Number(chosenPayer))
    ? Number(chosenPayer) : MERCARI_SHIPPING_PAYER.BUYER;
  const carrierId = Number(specifics.shipping?.carrierId);
  const classIds = Number.isInteger(carrierId) && carrierId > 0 ? [carrierId] : null;
  common.shipping = { payerId, ...(classIds ? { classIds, suggestedClassIds: classIds } : {}), ...(zip ? { zipCode: zip } : {}),
    dimensions: mercariDimensions(listing), ...(weight ? { weight: { weight } } : {}) };
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
  if (common.isAutoPriceDrop && !(Number.isInteger(Number(listing.mercari_specifics?.smartPricing?.minimumPrice))
    && common.minPriceForAutoPriceDrop > 0 && common.minPriceForAutoPriceDrop < common.price)) {
    missing.push({ field: 'minPriceForAutoPriceDrop', needs: 'a whole-dollar Smart Pricing floor below the listing price' });
  }
  if (!category) missing.push({ field: 'categoryId', needs: 'a Mercari category' });
  else if (category.parent) missing.push({ field: 'categoryId', needs: `a more specific Mercari category${listing.form?.overrides?.categoryV2?.children?.length
    ? ` (${listing.form.overrides.categoryV2.children.join(', ')})` : ''}` });
  if (common.conditionId == null) missing.push({ field: 'conditionId', needs: 'a condition' });
  if (category) {
    const size = mercariSize(listing, category.id);
    if (size.problem) missing.push({ field: 'sizeId', needs: size.problem });
    else if (size.none === 'unknown' && mercariWantedSize(listing)) missing.push({ field: 'sizeId', needs: 'its size list, which Anteroom could not read (try again)' });
  }
  if (!common.shipping.classIds) missing.push({ field: 'shippingClassIds', needs: 'a prepaid shipping label' });
  const weight = common.shipping.weight?.weight;
  if (!weight) missing.push({ field: 'weight', needs: 'a package weight' });
  else if (weight > MERCARI_MAX_LABEL_OZ) missing.push({ field: 'weight', needs: 'a package of 50 lb or less for a Mercari label' });
  return missing;
}

// Only an exact name from Mercari's own catalog is accepted. Encoded brand
// ids from other services are not a substitute for checking the name.
async function mercariBrandId(listing, deadline) {
  if (listing.mercari_specifics?.noBrand === true) return null;
  const name = String(listing.brand || '').trim();
  if (!name) return null;
  let id;
  try {
    id = await mercariBrandLookup(name, deadline);
  } catch (error) {
    marketplaceLog('mercari', 'brand', { error_code: 'unresolved' });
    throw marketplaceMissing('mercari', [{ field: 'brandId', needs: `its brand id for ${name}, which Anteroom could not read (try again)` }],
      String(error?.message || error));
  }
  if (!id) throw marketplaceMissing('mercari', [{ field: 'brandId', needs: `a brand from its list (${name} is not one)` }]);
  return id;
}

function mercariBrandLookup(name, deadline) {
  return mercariCatalogData(deadline).then(master => {
    const hit = master.itemBrands.find(entry => marketplaceNameKey(entry.name) === marketplaceNameKey(name));
    const id = Number(hit?.id);
    return Number.isInteger(id) && id > 0 ? id : null;
  });
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
  const own = list.map(entry => {
    const message = String(entry?.message || '');
    return message.match(/Field "[^"]+" is not defined by type "[^"]+"[^]*$/)?.[0] || message;
  }).filter(Boolean).join(' ');
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
  const write = !/Query$/.test(operationName) && !/^\s*query\b/.test(body?.query || '');
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
    categoryId: draft.itemCategory?.id ?? null, brandId: draft.brand?.id ?? null, sizeId: draft.itemSize?.id ?? null,
    payerId: draft.shippingPayer?.id ?? null,
    isAutoPriceDrop: draft.isAutoPriceDrop === true, minPriceForAutoPriceDrop: draft.minPriceForAutoPriceDrop,
    classIds: (draft.shippingClasses || []).map(entry => Number(entry?.id)).filter(classId => classId > 0),
    status: draft.draftItemStatus || null,
    actions_required: (draft.draftItemActionsRequired || []).map(action => String(action.field || '')).filter(Boolean) };
}

// What Mercari kept differently from what was sent, as the seller names it.
function mercariDraftDifferences(draft, common, expectedPhotos) {
  const differ = [];
  if (marketplaceText(draft.name) !== marketplaceText(common.name)) differ.push('title');
  if (common.description != null && marketplaceText(draft.description) !== marketplaceText(common.description)) differ.push('description');
  if (common.price != null && Number(draft.price) !== common.price) differ.push('price');
  if (expectedPhotos != null && draft.photo_count !== expectedPhotos) differ.push('photos');
  if (common.categoryId != null && Number(draft.categoryId) !== common.categoryId) differ.push('category');
  if (common.conditionId != null && Number(draft.conditionId) !== common.conditionId) differ.push('condition');
  if (common.brandId != null && Number(draft.brandId) !== common.brandId) differ.push('brand');
  if (Object.hasOwn(common, 'brandId') && common.brandId == null && draft.brandId != null) differ.push('brand');
  if (common.sizeId != null && Number(draft.sizeId) !== common.sizeId) differ.push('size');
  if (Boolean(draft.isAutoPriceDrop) !== Boolean(common.isAutoPriceDrop)
    || common.isAutoPriceDrop && Number(draft.minPriceForAutoPriceDrop) !== common.minPriceForAutoPriceDrop) differ.push('Smart Pricing');
  const sent = common.shipping.classIds || [];
  if (Number(draft.payerId) !== common.shipping.payerId || sent.length !== draft.classIds.length
      || sent.some(classId => !draft.classIds.includes(classId))) differ.push('shipping');
  return differ;
}

function mercariDraftMatches(draft, common, expectedPhotos) {
  return !mercariDraftDifferences(draft, common, expectedPhotos).length;
}

// The draft Mercari should hold for this listing, brand included.
async function mercariExpected(listing, deadline) {
  const common = mercariCommon(await mercariPreparedListing(listing, deadline));
  const brandId = await mercariBrandId(listing, deadline);
  if (brandId) common.brandId = brandId;
  return common;
}

function mercariDraftMismatch(draft, common, expectedPhotos, lead) {
  const differ = mercariDraftDifferences(draft, common, expectedPhotos);
  if (!differ.length) return null;
  const soyo = differ.includes('shipping') && common.shipping.classIds && !draft.classIds.length;
  const detail = soyo
    ? ' Mercari has it as ship-on-your-own with the seller paying, not the prepaid label Anteroom sent.'
    : '';
  return marketplaceError(`${lead} (${differ.join(', ')}).${detail} Nothing was listed. Check the draft and list again.`,
    { code: 'uncertain', marketplace_message: `draft differs: ${differ.join(', ')}` });
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
      if (!mercariDraftMatches(draft, await mercariExpected(payload.listing, payload.deadline), payload.expected_photo_count)) {
        return marketplaceFailure(new Error('Mercari\'s listing does not match this revision. List again.'), false, { draft_url: payload.draft_url });
      }
      return { ok: true, state: 'draft', draft_url: payload.draft_url };
    }
    const listing = await mercariPreparedListing(payload.listing, payload.deadline);
    const common = mercariCommon(listing);
    const missing = mercariMissing(listing, common);
    if (missing.length) throw marketplaceMissing('mercari', missing);
    const brandId = await mercariBrandId(payload.listing, payload.deadline);
    if (brandId) common.brandId = brandId;
    const photos = payload.photos || [];
    if (!photos.length || photos.length > MERCARI_MAX_PHOTOS) {
      throw marketplaceMissing('mercari', [{ field: 'photos', needs: `between one and ${MERCARI_MAX_PHOTOS} photos` }]);
    }
    // Mercari replaces the image list on edit too; leaving it out clears the draft's photos.
    common.imageIds = await mercariUploadPhotos(msg.job_id, photos, payload.deadline);
    const expectedPhotos = photos.length;
    if (id) {
      stage = 'writing';
      const data = await mercariRequest(mercariOperation('editDraftItemMutation', { input: { id, common } }), { deadline: payload.deadline });
      stage = 'written';
      if (String(data.editDraftItem?.id || '') !== id) throw new Error('Mercari did not confirm the draft update. Check the draft before trying again.');
    } else {
      stage = 'writing';
      const data = await mercariRequest(mercariOperation('addDraftItemMutation', { input: { common } }), { deadline: payload.deadline });
      stage = 'written';
      id = String(data.addDraftItem?.item?.id || '');
      if (!/^[A-Za-z0-9_-]{1,80}$/.test(id)) throw new Error('Mercari did not confirm a new draft. Check your Mercari drafts before trying again.');
      payload.draft_url = `https://www.mercari.com/sell/draft/${id}/`;
    }
    await storeReceipt({ receipt_url: payload.draft_url, expected_photo_count: expectedPhotos });
    const draft = await readMercariDraft(id, payload.deadline);
    const mismatch = mercariDraftMismatch(draft, common, expectedPhotos, 'Mercari did not keep everything this listing sent');
    if (mismatch) throw mismatch;
    return { ok: true, state: 'draft', draft_url: payload.draft_url };
  } catch (error) {
    // A draft mutation Mercari clearly refused wrote nothing.
    const safe = !recovery && (stage === 'prepare' || (stage === 'writing' && error.rejected === true));
    return marketplaceFailure(error, safe, { draft_url: payload.draft_url || null });
  }
}

const MERCARI_ACTION_LABELS = { categoryId: 'a category', conditionId: 'a condition', brandId: 'a brand', sizeId: 'a size',
  shipping: 'shipping', shippingClassIds: 'shipping', photos: 'photos', price: 'a price', name: 'a title', description: 'a description' };

// Publishing lists an existing READY draft with one listDraftItemMutation and
// the draft ID. Seller-confirmed only, sent at most once, and never retried
// when the reply is lost. The draft is read back first and must hold what
// this revision sends: category, brand, size, the prepaid label and who pays.
// Mercari's own drafts page also sends a ``createListingInput`` built from its
// form; the draft alone has listed correctly, so that input is not rebuilt.
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
    const listing = await mercariPreparedListing(payload.listing, payload.deadline);
    const common = mercariCommon(listing);
    const missing = mercariMissing(listing, common);
    if (missing.length) throw marketplaceMissing('mercari', missing);
    const brandId = await mercariBrandId(payload.listing, payload.deadline);
    if (brandId) common.brandId = brandId;
    const draft = await readMercariDraft(id, payload.deadline);
    const mismatch = mercariDraftMismatch(draft, common, null, 'Mercari\'s draft does not match this revision');
    if (mismatch) throw Object.assign(mismatch, { code: 'rejected' });
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
