// Read the seller's own active listings so Anteroom can import them. Read-only:
// every request is a GET, or a Mercari GraphQL query, and none of them edits,
// publishes or delists anything. Each listing is checked against the signed-in
// account before its details leave Chrome. Cookies and tokens stay here.
//
// The reads are the ones each site's own pages make (2026-10-26):
//   Poshmark  GET /vm-rest/users/{id}/posts/filtered (the closet, available only)
//             GET /vm-rest/posts/{id}, with /vm-rest/meta/catalog_v2 for category names
//   Mercari   publicProfileSearchQuery (on sale, itemStatuses [1]) and
//             sellFetchItemsDetails, the query its edit page uses; masterQuery names the ids
//   Depop     GET /api/v1/shop/{id}/products/ and /presentation/api/v1/products/by-slug/{slug}/,
//             with the public attributes list for brand, category and size names

const IMPORT_MARKETS = ['poshmark', 'mercari', 'depop'];
const IMPORT_LISTING_ID = {
  poshmark: /^[a-f0-9]{24}$/,
  mercari: /^m\d{6,}$/,
  depop: /^[a-z0-9][a-z0-9-]{0,200}$/,
};
const IMPORT_PAGE_SIZE = 48;
const IMPORT_MAX_PHOTOS = 20;
const IMPORT_MIN_GAP_MS = 1200;
const IMPORT_DEADLINE_MS = 60000;
const MERCARI_CONDITIONS = { 1: 'New', 2: 'Like new', 3: 'Good', 4: 'Fair', 5: 'Poor' };
const MERCARI_ITEM_DETAILS_QUERY = `query sellFetchItemsDetails($itemId: String!) {
  item(id: $itemId) {
    itemId: id
    name
    description
    photos { imageUrl }
    status
    itemCondition { id }
    price
    itemSize { id }
    brand { id }
    itemCategory { id }
    seller { id }
  }
}`;

// One request at a time per marketplace, a little over a second apart.
const importLast = {};
async function importPace(marketplace) {
  const wait = (importLast[marketplace] || 0) + IMPORT_MIN_GAP_MS + Math.floor(Math.random() * 600) - Date.now();
  if (wait > 0) {
    await new Promise(resolve => {
      const timer = setTimeout(resolve, wait);
      if (timer === 0) resolve();
    });
  }
  importLast[marketplace] = Date.now();
}

function importError(message, code = 'rejected') {
  return marketplaceError(message, { code });
}

// A GET through the shared fetch: timeouts, retries of safe reads, and the
// sign-in and bot-check errors every marketplace action reports.
// Depop's API takes the bearer token rather than cookies, as its own site does.
async function importGet(marketplace, url, deadline, headers = {}) {
  await importPace(marketplace);
  const { data } = await marketplaceFetch(url, { marketplace, step: 'import', method: 'GET', deadline, timeoutMs: 20000,
    credentials: marketplace === 'depop' ? 'omit' : 'include', headers: { Accept: 'application/json', ...headers } });
  return data;
}

function importText(value, limit = 20000) {
  return typeof value === 'string' ? value.replace(/\r\n?/g, '\n').trim().slice(0, limit) : '';
}

function importMoney(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? Math.round(number * 100) / 100 : null;
}

// A shipping price, where 0 is free shipping and null or blank is not offered.
function importShippingCost(value) {
  if (value == null || (typeof value === 'string' && !value.trim()) || typeof value === 'boolean') return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? Math.round(number * 100) / 100 : null;
}

function importCount(value) {
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 ? number : null;
}

function importPhotos(urls) {
  const seen = new Set();
  const out = [];
  for (const raw of urls) {
    if (typeof raw !== 'string') continue;
    let url;
    try { url = new URL(raw.startsWith('//') ? `https:${raw}` : raw); } catch { continue; }
    if (url.protocol !== 'https:' || seen.has(url.href)) continue;
    seen.add(url.href);
    out.push(url.href);
    if (out.length === IMPORT_MAX_PHOTOS) break;
  }
  return out;
}

function importSame(a, b) {
  return a != null && b != null && String(a) !== '' && String(a) === String(b);
}

// Depop has no title; its first line of description stands in for one.
function importTitleFromText(text) {
  const line = importText(text).split('\n').map(part => part.replace(/#\S+/g, '').trim()).find(Boolean) || '';
  return line.length > 80 ? `${line.slice(0, 77).trimEnd()}…` : line;
}

// ---- Poshmark ----

async function poshmarkImportPage(cursor, deadline) {
  const { user_id: account } = await poshmarkAccount();
  const request = { filters: { department: 'All', inventory_status: ['available'] }, sort_by: 'added_desc', experience: 'all',
    count: IMPORT_PAGE_SIZE, ...(cursor ? { max_id: cursor } : {}) };
  const body = await importGet('poshmark', `${POSHMARK}/vm-rest/users/${account}/posts/filtered?request=${encodeURIComponent(JSON.stringify(request))}&summarize=true&${POSHMARK_QUERY}`, deadline);
  if (!Array.isArray(body?.data)) throw marketplaceError('Poshmark answered in a way Anteroom does not recognise.', { code: 'schema_drift' });
  const rows = body.data.filter(post => IMPORT_LISTING_ID.poshmark.test(String(post?.id ?? ''))).map(post => ({
    id: String(post.id), url: poshmarkListingUrl(post.id), title: importText(post.title, 200) || String(post.id),
    price: post.price_amount?.currency_code && post.price_amount.currency_code !== 'USD' ? null : importMoney(post.price_amount?.val ?? post.price),
    quantity: poshmarkQuantity(post),
  }));
  const next = typeof body.more?.next_max_id === 'string' && body.more.next_max_id ? body.more.next_max_id : null;
  return { rows, next_cursor: rows.length ? next : null, account: String(account) };
}

function poshmarkQuantity(post) {
  const rows = post?.inventory?.size_quantities;
  if (!Array.isArray(rows) || !rows.length) return null;
  const counts = rows.map(row => importCount(row?.quantity_available));
  return counts.every(count => count != null) ? counts.reduce((sum, count) => sum + count, 0) : null;
}

function poshmarkCategoryNames(post, catalog) {
  const ids = post.catalog || {};
  const department = (catalog?.catalog?.departments || []).find(entry => entry.id === ids.department);
  const category = (department?.categories || []).find(entry => entry.id === ids.category);
  const featureId = Array.isArray(ids.category_features) ? String(ids.category_features[0]?.id ?? ids.category_features[0] ?? '') : '';
  const feature = featureId ? (category?.category_features || []).find(entry => entry.id === featureId) : null;
  const names = [department?.display ?? ids.department_obj?.display, category?.display ?? ids.category_obj?.display,
    feature?.display ?? ids.category_feature_objs?.[0]?.display].map(name => importText(name, 120)).filter(Boolean);
  return { path: names.join(' > '), id: [ids.department, ids.category, featureId].filter(Boolean).map(String).join('/') };
}

async function poshmarkImportListing(id, deadline) {
  const { user_id: account } = await poshmarkAccount();
  const body = await importGet('poshmark', `${POSHMARK}/vm-rest/posts/${id}?${POSHMARK_QUERY}`, deadline);
  const post = body?.data || body;
  if (String(post?.id ?? '') !== id) throw importError('Poshmark did not return this listing.');
  if (!importSame(post.creator_id, account)) throw importError('This listing does not belong to the signed-in Poshmark account.');
  if (post.inventory?.status !== 'available') throw importError('This Poshmark listing is not available for sale.');
  if (post.price_amount?.currency_code && post.price_amount.currency_code !== 'USD') throw importError('This import supports listings priced in USD.');
  const sizes = Array.isArray(post.inventory?.size_quantities) ? post.inventory.size_quantities : [];
  let catalog = null;
  try { catalog = await poshmarkCatalogData(deadline); } catch { /* Names come from the post when the catalog cannot be read. */ }
  const category = poshmarkCategoryNames(post, catalog);
  const size = sizes.length === 1 ? sizes[0] : {};
  return {
    account: String(account),
    id, url: poshmarkListingUrl(id), title: importText(post.title, 200), description: importText(post.description),
    price: importMoney(post.price_amount?.val), currency: 'USD', quantity: poshmarkQuantity(post),
    brand: importText(post.brand, 120) || null,
    size: importText(size.size_obj?.display ?? size.size_obj?.display_with_size_set ?? size.size_id, 80) || null,
    condition: importText(post.condition, 40) || null,
    category_path: category.path || null, category_id: category.id || null,
    sku: importText(post.seller_private_info?.sku ?? size.seller_inventory_private_info?.sku, 80) || null,
    colors: poshmarkImportColors(post),
    photos: importPhotos([post.cover_shot, ...(Array.isArray(post.pictures) ? post.pictures : [])]
      .map(picture => picture?.url_large || picture?.url || picture?.url_small)),
    poshmark: poshmarkImportFields(post, sizes),
  };
}

function poshmarkImportColors(post) {
  return (Array.isArray(post.colors) ? post.colors : []).map(color => importText(color?.name ?? color, 40)).filter(Boolean);
}

// The rest of Poshmark's form, in the post's own shape, so the Poshmark tab
// shows the listing as Poshmark does and an update sends the same values back.
function poshmarkImportFields(post, sizes) {
  const condition = String(post.condition || '');
  const discount = String(post.seller_shipping_discount?.id ?? '');
  const own = post.seller_private_info || {};
  return {
    availability: importText(post.inventory?.status, 40) || null,
    multi_item: typeof post.inventory?.multi_item === 'boolean' ? post.inventory.multi_item : null,
    condition: POSHMARK_CONDITIONS.has(condition) ? condition : null,
    colors: poshmarkImportColors(post).slice(0, 2),
    style_tags: (Array.isArray(post.style_tags) ? post.style_tags : []).map(tag => importText(tag?.name ?? tag, 25)).filter(Boolean).slice(0, 3),
    original_price: importMoney(post.original_price_amount?.val),
    shipping_discount_id: /^[a-f0-9]{24}$/.test(discount) ? discount : null,
    sku: importText(own.sku, 80) || null,
    cost: importMoney(own.cost_price_amount?.val),
    other_info: importText(own.other_info, 2000) || null,
    smart_sell: { enabled: post.offer_auto_actions_v2_enabled === true,
      minimum_price: importMoney(post.offer_auto_actions_min_price_amount?.val) },
    sizes: sizes.map(row => ({
      size_id: importText(String(row?.size_id ?? row?.size_obj?.id ?? ''), 80),
      size_set_tags: (Array.isArray(row?.size_set_tags) ? row.size_set_tags : []).map(tag => importText(String(tag), 40)).filter(Boolean),
      size_system: importText(row?.size_system ?? row?.size_obj?.size_system, 10) || 'us',
      quantity: importCount(row?.quantity_available),
      sku: importText(row?.seller_inventory_private_info?.sku, 80),
    })),
  };
}

// ---- Mercari ----

async function mercariImportPage(cursor, deadline) {
  const session = await mercariCurrentSession(deadline, true);
  const seller = Number(session.user.userId);
  const offset = Number.isInteger(Number(cursor)) && Number(cursor) > 0 ? Number(cursor) : 0;
  await importPace('mercari');
  const data = await mercariRequest({ operationName: 'publicProfileSearchQuery',
    variables: { criteria: { itemStatuses: [1], sellerIds: [seller], length: IMPORT_PAGE_SIZE, offset }, userId: seller },
    extensions: { persistedQuery: { version: 1, sha256Hash: MERCARI_PROFILE_SEARCH } } }, { deadline, retry: true });
  const items = data?.search?.itemsList;
  if (!Array.isArray(items)) throw marketplaceError('Mercari answered in a way Anteroom does not recognise.', { code: 'schema_drift' });
  const rows = items.filter(item => item?.status === 'on_sale' && IMPORT_LISTING_ID.mercari.test(String(item.id))).map(item => ({
    id: String(item.id), url: `https://www.mercari.com/us/item/${item.id}/`, title: importText(item.name, 200) || String(item.id),
    price: importMoney(Number(item.price) / 100), quantity: 1,
  }));
  return { rows, next_cursor: items.length >= IMPORT_PAGE_SIZE ? String(offset + items.length) : null, account: String(seller) };
}

function mercariCategoryPath(master, id) {
  const byId = new Map(master.itemCategories.map(row => [String(row.id), row]));
  const names = [];
  const seen = new Set();
  for (let current = byId.get(String(id)); current && !seen.has(current.id); current = byId.get(String(current.parentId))) {
    seen.add(current.id);
    names.unshift(importText(current.name, 120));
  }
  return names.filter(Boolean).join(' > ');
}

async function mercariImportListing(id, deadline) {
  const session = await mercariCurrentSession(deadline, true);
  await importPace('mercari');
  const data = await mercariRequest({ operationName: 'sellFetchItemsDetails', variables: { itemId: id }, query: MERCARI_ITEM_DETAILS_QUERY },
    { deadline, retry: true });
  const item = data?.item;
  if (String(item?.itemId ?? '') !== id) throw importError('Mercari did not return this listing.');
  if (!importSame(item.seller?.id, session.user.userId)) throw importError('This listing does not belong to the signed-in Mercari account.');
  if (item.status !== 'on_sale') throw importError('This Mercari listing is not on sale.');
  let master = null;
  try { master = await mercariCatalogData(deadline); } catch { /* Ids stay without names. */ }
  const name = (rows, value, key = 'name') => importText(rows?.find(row => String(row.id) === String(value))?.[key], 120) || null;
  const brandId = item.brand?.id;
  return {
    account: String(session.user.userId),
    id, url: `https://www.mercari.com/us/item/${id}/`, title: importText(item.name, 200), description: importText(item.description),
    price: importMoney(Number(item.price) / 100), currency: 'USD', quantity: 1,
    brand: brandId != null && Number(brandId) !== 0 ? name(master?.itemBrands, brandId) : null,
    size: item.itemSize?.id != null ? name(master?.itemSizes, item.itemSize.id, 'title') : null,
    condition: MERCARI_CONDITIONS[Number(item.itemCondition?.id)] || null,
    category_path: master && item.itemCategory?.id != null ? mercariCategoryPath(master, item.itemCategory.id) || null : null,
    category_id: item.itemCategory?.id != null ? String(item.itemCategory.id) : null,
    sku: null, colors: [],
    photos: importPhotos((Array.isArray(item.photos) ? item.photos : []).map(photo => photo?.imageUrl)),
  };
}

// ---- Depop ----

async function depopImportAccount(deadline) {
  const me = await importGet('depop', `${DEPOP_WEBAPI}/presentation/api/v1/users/me/`, deadline, { Authorization: await depopBearer() });
  const id = String(me?.id ?? '');
  if (!/^\d{1,15}$/.test(id)) throw marketplaceError('Sign in to Depop in Chrome first.', { code: 'signed_out' });
  return id;
}

async function depopImportPage(cursor, deadline) {
  const shop = await depopImportAccount(deadline);
  const token = cursor ? depopCursorToken(cursor) : '';
  const body = await importGet('depop', `${DEPOP_WEBAPI}/api/v1/shop/${shop}/products/?limit=${IMPORT_PAGE_SIZE}${token ? `&offset_id=${token}` : ''}`,
    deadline, { Authorization: await depopBearer() });
  const products = depopProductRows(body);
  if (!Array.isArray(body?.objects) && !Array.isArray(body?.products) && !Array.isArray(body?.data)) {
    throw marketplaceError('Depop answered in a way Anteroom does not recognise.', { code: 'schema_drift' });
  }
  const rows = products.filter(product => (product?.status == null || product.status === 'STATUS_ONSALE')
      && product?.sold !== true && IMPORT_LISTING_ID.depop.test(String(product?.slug ?? '')))
    .map(product => ({ id: String(product.slug), url: depopListingUrl(product.slug),
      title: importTitleFromText(product.description) || String(product.slug), price: importMoney(depopProductPrice(product)),
      quantity: importCount(product.quantity) }));
  const next = body?.meta?.end === true ? '' : depopCursorToken(body?.meta?.last_offset_id);
  return { rows, next_cursor: products.length && next && next !== token ? decodeURIComponent(next) : null, account: shop };
}

function depopImportVariants(product) {
  // Depop's product document names its sizes variants_all, each with a variant name.
  const raw = product?.variants ?? product?.variants_all;
  const variants = raw && typeof raw === 'object' ? raw : null;
  if (Array.isArray(variants)) {
    return variants.map(row => ({ id: String(row?.id ?? row?.size_id ?? ''), quantity: importCount(row?.quantity),
      name: row?.name ?? row?.size ?? row?.variant }));
  }
  return variants ? Object.entries(variants).map(([key, value]) => ({ id: key, quantity: importCount(value?.quantity ?? value), name: value?.name })) : [];
}

// Depop's product document (2026-10-30) no longer carries a price; its shop
// list still does. Page through the shop until this listing turns up.
async function depopShopRow(shop, slug, deadline) {
  let token = '';
  for (let page = 0; page < 50; page += 1) {
    const body = await importGet('depop', `${DEPOP_WEBAPI}/api/v1/shop/${shop}/products/?limit=${IMPORT_PAGE_SIZE}${token ? `&offset_id=${token}` : ''}`,
      deadline, { Authorization: await depopBearer() });
    const rows = depopProductRows(body);
    const row = rows.find(product => String(product?.slug ?? '') === slug);
    if (row) return row;
    const next = body?.meta?.end === true ? '' : depopCursorToken(body?.meta?.last_offset_id);
    if (!rows.length || !next || next === token) return null;
    token = next;
  }
  return null;
}

async function depopImportListing(slug, deadline) {
  const account = await depopImportAccount(deadline);
  const product = await importGet('depop', `${DEPOP_WEBAPI}/presentation/api/v1/products/by-slug/${slug}/`, deadline,
    { Authorization: await depopBearer() });
  if (String(product?.slug ?? '') !== slug) throw importError('Depop did not return this listing.');
  if (!importSame(product.user_id ?? product.seller?.id ?? product.user?.id, account)) throw importError('This listing does not belong to the signed-in Depop account.');
  if (product.status !== 'STATUS_ONSALE' || product.sold === true) throw importError('This Depop listing is not on sale.');
  let listed = depopProductPrice(product);
  let shopCurrency = null;
  if (listed == null) {
    const row = await depopShopRow(account, slug, deadline);
    listed = row ? depopProductPrice(row) : null;
    shopCurrency = row?.price?.currency_name ?? row?.price_currency ?? null;
  }
  const currency = String(product.price_currency ?? product.pricing?.currency_name ?? product.currency ?? shopCurrency ?? 'USD').toUpperCase();
  if (currency !== 'USD') throw importError('This import supports listings priced in USD.');
  const variants = depopImportVariants(product);
  if (variants.length > 1) throw importError('Listings with several sizes are not supported by this import yet.');
  let attributes = null;
  try { attributes = await depopAttributes(deadline); } catch { /* Names stay empty when Depop's list cannot be read. */ }
  const brandId = depopProductBrand(product);
  const brands = Array.isArray(attributes?.brand) ? attributes.brand : attributes?.brands || [];
  const brand = brandId ? depopName(brands.find(entry => String(entry.id) === brandId)) || null : null;
  const type = depopProductType(product);
  const department = depopImportDepartment(product, type, attributes);
  const category = attributes && department && type ? depopProduct(attributes, department, type) : null;
  const group = (attributes?.group || []).find(entry => entry.id === category?.group)
    || (attributes?.group || []).find(entry => (entry.product_types || []).some(row => row.id === type));
  const typeName = depopName(group?.product_types?.find(row => row.id === type)) || type.replace(/[-_]+/g, ' ');
  const departmentName = depopName(attributes?.department?.find(entry => entry.id === department)) || department;
  const variant = variants[0];
  const sizeSet = (attributes?.size_sets || []).find(entry => String(entry.id) === String(product.variant_set ?? product.variant_set_id ?? ''));
  const size = variant ? importText(String(variant.name ?? depopName(sizeSet?.sizes?.find(entry => String(entry.id) === variant.id)) ?? ''), 80) || null : null;
  const pictures = depopPictureRows(product.pictures ?? product.pictures_data ?? product);
  const shipping = await depopImportShipping(product, category, deadline);
  return {
    account,
    id: slug, url: depopListingUrl(slug), title: importTitleFromText(product.description), description: importText(product.description),
    price: importMoney(listed), currency: 'USD',
    quantity: variant ? variant.quantity : importCount(product.quantity),
    brand: brand && !/^other$/i.test(brand) ? importText(brand, 120) : null, size,
    condition: importText(typeof product.condition === 'object' ? product.condition?.id : product.condition, 40) || null,
    category_path: [departmentName, depopName(group), typeName].map(name => importText(name, 120)).filter(Boolean).join(' > ') || null,
    // Depop's own category id, only when its live list has that product type under that department.
    category_id: category ? category.leaf : null,
    department: depopImportGeneralDepartment(department, product),
    sku: importText(product.sku, 80) || null,
    colors: depopImportIds(product.colour ?? product.colours ?? product.color).slice(0, 2),
    photos: importPhotos(pictures.map(row => (row && typeof row === 'object'
      ? row.url || row.formats?.P0?.url || row.formats?.P8?.url || Object.values(row.formats || {})[0]?.url : null))),
    // Depop's own ids; Anteroom names them as the sell page does.
    style: depopImportIds(product.style).slice(0, 3), age: depopImportIds(product.age).slice(0, 1), source: depopImportIds(product.source).slice(0, 2),
    attributes: depopImportAttributes(product, category, attributes),
    ...shipping,
    boost: depopImportBoost(product),
    hashtags: depopImportHashtags(product),
  };
}

const DEPOP_DEPARTMENTS = ['womenswear', 'menswear', 'kidswear', 'everything-else'];

// Depop's product says male/female/unisex and whether it is kids', the way its
// sell page posts them. Its department is womenswear, menswear or kidswear from
// those, and for anything else the one department whose groups list the type.
function depopImportDepartment(product, type, attributes) {
  const named = [product.department, product.attributes?.department, product.gender]
    .map(value => String(value ?? '').trim().toLowerCase()).find(value => DEPOP_DEPARTMENTS.includes(value));
  if (named) return named;
  const kids = [product.is_kids, product.isKids, product.attributes?.is_kids].includes(true);
  const gender = String(product.gender ?? product.attributes?.gender ?? '').trim().toLowerCase();
  if (kids) return 'kidswear';
  if (gender === 'male') return 'menswear';
  if (gender === 'female') return 'womenswear';
  const listed = [...new Set((attributes?.group || []).filter(entry => (entry.product_types || []).some(row => row.id === type))
    .flatMap(entry => (Array.isArray(entry.department) ? entry.department : [])))];
  if (listed.includes('everything-else')) return 'everything-else';
  return listed.length === 1 ? listed[0] : '';
}

// General's department, which Depop's List reads back for a kids' gender.
function depopImportGeneralDepartment(department, product) {
  const gender = String(product.gender ?? product.attributes?.gender ?? '').trim().toLowerCase();
  if (department === 'menswear') return 'Men';
  if (department === 'womenswear') return 'Women';
  if (department === 'kidswear') return gender === 'male' ? 'Boys' : gender === 'female' ? 'Girls' : null;
  return null;
}

// A list of Depop ids, written as strings or as { id }.
function depopImportIds(value) {
  const rows = Array.isArray(value) ? value : value == null || value === '' ? [] : [value];
  return [...new Set(rows.map(row => importText(typeof row === 'object' && row ? depopFieldId(row) || String(row.name ?? '') : String(row ?? ''), 60))
    .filter(Boolean))];
}

// Material, occasion, body fit and the type attribute, named as Depop's sell page names them.
function depopImportAttributes(product, category, attributes) {
  const map = depopAttributeMap(product.attributes);
  const out = {};
  for (const id of category?.attributeIds || []) {
    const values = depopImportIds(map[id]);
    if (!values.length) continue;
    const definition = (attributes?.generic_attributes || []).find(entry => entry.id === id);
    out[id] = values.map(value => {
      const option = (definition?.attributes || []).find(row => String(row.id) === value);
      return importText(option ? depopPageName(option) || depopName(option) : value, 60);
    }).filter(Boolean);
  }
  return out;
}

// The package size (the sell page's title for Depop's parcel id), or the
// seller's own domestic price when the listing does not use Depop Shipping,
// and the worldwide price when it ships abroad.
async function depopImportShipping(product, category, deadline) {
  const methods = Array.isArray(product.shipping_methods ?? product.shippingMethods) ? product.shipping_methods ?? product.shippingMethods : [];
  const parcelId = importText(String(methods.find(row => row?.parcel_size ?? row?.parcelSize)?.parcel_size
    ?? methods.find(row => row?.parcelSize)?.parcelSize ?? ''), 60);
  let parcel = parcelId || null;
  if (parcelId) {
    try {
      const params = depopShippingParams(category);
      const providers = await importGet('depop', `${DEPOP_WEBAPI}/presentation/api/v1/shipping/providers/depop/?${params}`, deadline,
        { Authorization: await depopBearer() });
      parcel = importText(depopParcelRows(providers).find(row => row.id === parcelId)?.title, 60) || parcelId;
    } catch { /* The id stays; Depop's List matches a package size by id too. */ }
  }
  const national = importShippingCost(product.national_shipping_cost ?? product.nationalShippingCost);
  const worldwide = importShippingCost(product.international_shipping_cost ?? product.internationalShippingCost);
  return { parcel_size: parcel, domestic_shipping: parcel ? null : national, worldwide_shipping: worldwide };
}

function depopImportBoost(product) {
  const boost = product.boost ?? product.boosted ?? product.is_boosted;
  if (typeof boost === 'boolean') return boost;
  const status = boost && typeof boost === 'object' ? boost.status : null;
  return status === 'active' ? true : status === 'inactive' ? false : null;
}

// Depop's hashtags, or the ones written in the description, without the #.
function depopImportHashtags(product) {
  const listed = Array.isArray(product.hashtags) ? product.hashtags.map(String)
    : (importText(product.description).match(/#[A-Za-z0-9_]+/g) || []);
  return [...new Set(listed.map(tag => tag.replace(/^#/, '').replace(/[^A-Za-z0-9_]+/g, '').toLowerCase()).filter(Boolean))].slice(0, 5);
}

const IMPORT_READERS = {
  poshmark: { page: poshmarkImportPage, listing: poshmarkImportListing },
  mercari: { page: mercariImportPage, listing: mercariImportListing },
  depop: { page: depopImportPage, listing: depopImportListing },
};

function importFailure(error) {
  return { ok: false, error: String(error?.message || 'Could not read this marketplace.'), error_code: error?.code || 'rejected' };
}

async function readMarketplaceImportPage(marketplace, cursor) {
  if (!IMPORT_MARKETS.includes(marketplace)) return { ok: false, error: 'Unsupported marketplace.', error_code: 'rejected' };
  if (nativeOperationsRunning()) return { ok: false, error: 'Wait for the current listing action to finish.', error_code: 'busy' };
  try {
    const clean = typeof cursor === 'string' && cursor.length <= 300 ? cursor : null;
    const page = await IMPORT_READERS[marketplace].page(clean, Date.now() + IMPORT_DEADLINE_MS);
    // The signed-in account the page was read from, so a run that continues across pages can tell it changed.
    return { ok: true, rows: page.rows, next_cursor: page.next_cursor, account: page.account };
  } catch (error) {
    return importFailure(error);
  }
}

async function readMarketplaceImportListing(marketplace, listingId) {
  if (!IMPORT_MARKETS.includes(marketplace)) return { ok: false, error: 'Unsupported marketplace.', error_code: 'rejected' };
  const id = marketplace === 'poshmark' ? String(listingId || '').toLowerCase() : String(listingId || '');
  if (!IMPORT_LISTING_ID[marketplace].test(id)) return { ok: false, error: 'This is not a listing ID for that marketplace.', error_code: 'rejected' };
  if (nativeOperationsRunning()) return { ok: false, error: 'Wait for the current listing action to finish.', error_code: 'busy' };
  try {
    return { ok: true, listing: await IMPORT_READERS[marketplace].listing(id, Date.now() + IMPORT_DEADLINE_MS) };
  } catch (error) {
    return importFailure(error);
  }
}

async function handleMarketplaceImport(msg) {
  const payload = msg.payload || {};
  try {
    sendBrowserResult(msg, msg.type === 'marketplace.import.listing'
      ? await readMarketplaceImportListing(payload.marketplace, payload.listing_id)
      : await readMarketplaceImportPage(payload.marketplace, payload.cursor));
  } catch {
    sendBrowserResult(msg, { ok: false, error: 'Could not read this marketplace.', error_code: 'rejected' });
  }
}
