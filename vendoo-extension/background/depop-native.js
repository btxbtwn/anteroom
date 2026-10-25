// Independently inspected Depop web request builders (2026-10-24).
// Session material stays in the extension worker. No marketplace tab is used.

const DEPOP_COLOURS = new Set(['black', 'grey', 'white', 'brown', 'tan', 'cream', 'yellow', 'red', 'burgundy', 'pink', 'purple', 'blue', 'navy', 'green', 'khaki', 'orange', 'silver', 'gold', 'multi']);

const DEPOP_CONDITIONS = new Set(['brand_new', 'used_like_new', 'used_excellent', 'used_good', 'used_fair']);

// Studio's form already carries Depop's own slug; a plain label is mapped the way Vendoo maps it.
function depopCondition(value) {
  const text = String(value || '').trim().toLowerCase();
  if (DEPOP_CONDITIONS.has(text)) return text;
  if (/defect|imperfection/.test(text)) return 'used_good';
  if (/fair|poor/.test(text)) return 'used_fair';
  if (/excellent/.test(text)) return 'used_excellent';
  if (/like new|nwot|new without/.test(text)) return 'used_like_new';
  if (/brand new|nwt|new with tags|^new\b/.test(text)) return 'brand_new';
  if (/good|pre-owned|preowned|used/.test(text)) return 'used_good';
  return '';
}

function depopColour(value) {
  const name = String(value || '').trim().toLowerCase().replace('gray', 'grey').replace('beige', 'tan')
    .replace(/^multi[- ]?colou?r(?:ed)?$/, 'multi');
  return DEPOP_COLOURS.has(name) ? name : '';
}

// Age, style, and source use the same codes Vendoo writes. Colour and condition
// are Depop's own slugs. Brand stays on the draft until Depop's brand id is known.
function depopFormFields(listing) {
  const specifics = listing?.form?.specifics || {};
  const fields = {};
  const colour = [listing?.primaryColor, listing?.secondaryColor].map(depopColour).filter(Boolean);
  if (colour.length) fields.colour = [...new Set(colour)].slice(0, 2);
  const condition = depopCondition(listing?.form?.overrides?.condition || listing?.condition);
  if (condition) fields.condition = condition;
  for (const key of ['age', 'source', 'style']) {
    const values = Array.isArray(specifics[key]) ? specifics[key].map(String).filter(Boolean) : [];
    if (values.length) fields[key] = values.slice(0, 3);
  }
  return fields;
}

// Anteroom stores Depop's own category id, such as kidswear_jackets. The draft
// wants the product type, whether it is kids', and male/female/unisex.
function depopCategoryFields(listing) {
  const id = String(listing?.direct_category_ids?.depop || listing?.depop_specifics?.categoryId || listing?.marketplace_category_ids?.depop || '');
  const match = /^(kidswear|menswear|womenswear)_(.+)$/.exec(id);
  if (!match) return null;
  const kids = match[1] === 'kidswear';
  const listed = String(listing?.department || '');
  const gender = kids
    ? (/boy/i.test(listed) ? 'male' : /girl/i.test(listed) ? 'female' : 'unisex')
    : (match[1] === 'menswear' ? 'male' : 'female');
  return { product_type: match[2], is_kids: kids, gender };
}

function depopBrandLabel(row) {
  return String(row?.name_i18n?.['en-US'] ?? row?.name ?? row?.title ?? '').trim();
}

// Depop's catch-all brand. A catalog row named Other wins; otherwise the id Depop uses.
function depopOtherId(rows) {
  const other = rows.find(row => {
    const id = String(row.id || '').trim().toLowerCase();
    return id === 'other' || id === 'unbranded' || depopBrandLabel(row).toLowerCase() === 'other';
  });
  return other ? String(other.id) : 'other';
}

// Brand on the draft is Depop's brand id. "Columbia" is Columbia Sportswear
// when that is the only brand the word starts. Anything Depop does not list,
// including a blank brand, is Other.
function depopBrandId(brands, name) {
  const wanted = String(name || '').trim().toLowerCase();
  const rows = (Array.isArray(brands) ? brands : []).filter(row => row?.id && row.status !== 'inactive');
  if (wanted && wanted !== 'other') {
    const exact = rows.filter(row => [depopBrandLabel(row), row.id].some(value => String(value || '').trim().toLowerCase() === wanted));
    if (exact.length === 1) return String(exact[0].id);
    const prefix = rows.filter(row => depopBrandLabel(row).toLowerCase().startsWith(`${wanted} `));
    if (prefix.length === 1) return String(prefix[0].id);
  }
  return depopOtherId(rows);
}

// Depop's parcel titles are the same words Anteroom stores (Small, Medium, …).
function depopParcelSize(sizes, listing) {
  const wanted = String(listing?.depop_specifics?.parcelSize || listing?.depop_specifics?.parcel_size || '').trim().toLowerCase();
  if (!wanted || !Array.isArray(sizes)) return null;
  return sizes.find(size => String(size?.title || '').trim().toLowerCase() === wanted) || null;
}

// Category, brand, country-level location, and Depop Shipping. Each lookup is
// skipped when Depop does not answer, so publish can still name what is missing.
async function depopListingFields(request, listing, deadline) {
  const fields = {};
  const category = depopCategoryFields(listing);
  if (category) Object.assign(fields, category);
  try {
    const attributes = await request('GET', '/presentation/api/v1/attributes/');
    const brand = depopBrandId(attributes?.brand, listing?.depop_specifics?.brand || listing?.form?.overrides?.brand || listing?.brand);
    if (brand) fields.brand = brand;
  } catch { /* The publish check names a missing brand. */ }
  try {
    const account = await request('GET', '/presentation/api/v1/users/me/');
    const code = String(account?.country || '').toUpperCase();
    if (/^[A-Z]{2}$/.test(code)) {
      const { data } = await marketplaceFetch('https://assets.depop.com/web/assets/listing/location/countries.json', {
        marketplace: 'depop', step: 'location', credentials: 'omit', deadline, retry: true,
      });
      const country = (Array.isArray(data) ? data : []).find(row => row?.countryCode === code);
      if (country?.address && country.country && Number.isFinite(Number(country.geoLat)) && Number.isFinite(Number(country.geoLng))) {
        Object.assign(fields, {
          address: country.address, country: country.country, country_code: country.countryCode,
          geo_lat: Number(country.geoLat), geo_lng: Number(country.geoLng),
        });
      }
    }
  } catch { /* The publish check names a missing location. */ }
  try {
    const settings = await request('GET', '/presentation/api/v1/users/me/settings/shipping/');
    const active = (settings?.shipping_providers || []).filter(row => row?.active && row.id);
    const provider = active.find(row => row.id === 'USPS') || (active.length === 1 ? active[0] : null);
    const addresses = await request('GET', '/presentation/api/v1/addresses/');
    const from = (Array.isArray(addresses) ? addresses : [])
      .filter(row => row?.id != null)
      .sort((a, b) => String(b.last_modified || '').localeCompare(String(a.last_modified || '')))[0];
    const price = Number(listing?.price);
    if (provider && from && fields.country_code && Number.isFinite(price)) {
      const params = new URLSearchParams({
        ids: String(provider.id), item_cost: price.toFixed(2), item_currency: 'USD',
      });
      if (category?.product_type) params.set('product_type', category.product_type);
      if (category?.gender) params.set('gender', category.gender);
      const quoted = await request('GET', `/presentation/api/v1/shipping/providers/depop/?${params}`, undefined, {
        'x-country-code': fields.country_code,
      });
      const quote = (Array.isArray(quoted) ? quoted : []).find(row => row?.id === provider.id);
      const size = depopParcelSize(quote?.parcel_sizes, listing);
      if (size?.id) {
        fields.shipping_methods = [{
          payer: 'buyer', parcel_size: String(size.id), shipping_provider_id: String(provider.id),
          ship_from_address_id: from.id,
        }];
        if (size.cost?.amount != null && size.cost.amount !== '') fields.national_shipping_cost = String(size.cost.amount);
      }
    }
  } catch { /* The publish check names missing shipping. */ }
  return fields;
}

// Depop's names for the fields a draft still lacks, in plain words.
const DEPOP_FIELD_NEEDS = { brand: 'a brand', brand_id: 'a brand', category: 'a category', category_id: 'a category',
  product_type: 'a category', group: 'a category', condition: 'a condition', colour: 'a colour', size: 'a size',
  variants: 'a size', variant_set: 'a size', shipping: 'shipping', shipping_methods: 'shipping',
  national_shipping: 'shipping', parcel_size: 'a parcel size', location: 'a location',
  address: 'a shipping address', age: 'an age', style: 'a style', source: 'a source', gender: 'a gender', pictures: 'photos',
  photos: 'photos', price: 'a price', price_amount: 'a price', description: 'a description', quantity: 'a quantity' };

function depopFieldNeeds(field) {
  return DEPOP_FIELD_NEEDS[field] || String(field).replace(/[_-]+/g, ' ').trim() || 'more details';
}

// Depop's own limit for the description, which carries the title too.
const DEPOP_MAX_DESCRIPTION = 1000;
const DEPOP_MAX_HASHTAGS = 5;
const DEPOP_PHOTO_HOSTS = ['.s3.amazonaws.com', '.s3-accelerate.amazonaws.com'];
const DEPOP_WEBAPI = 'https://webapi.depop.com';

function depopBaseDescription(listing) {
  return [String(listing?.title || '').trim(), String(listing?.description || '').trim()].filter(Boolean).join('\n\n');
}

// Title, description, then up to five of the listing's tags as hashtags,
// the way Vendoo writes them, as many as fit Depop's limit.
function depopDescription(listing) {
  let text = depopBaseDescription(listing).slice(0, DEPOP_MAX_DESCRIPTION);
  const tags = listingTags(listing).map(tag => tag.replace(/[^A-Za-z0-9]+/g, '').toLowerCase()).filter(Boolean);
  const chosen = [...new Set(tags)].slice(0, DEPOP_MAX_HASHTAGS).map(tag => `#${tag}`);
  while (chosen.length && `${text}\n\n${chosen.join(' ')}`.length > DEPOP_MAX_DESCRIPTION) chosen.pop();
  if (chosen.length && !/#\w/.test(text)) text = `${text}\n\n${chosen.join(' ')}`;
  return text;
}

const depopName = entry => String(entry?.name_i18n?.['en-US'] ?? entry?.name ?? entry?.title ?? entry?.id ?? '');

// Depop's taxonomy. The partner host api.depop.com/api/v3/attributes/ answers
// 403 "not authorized" without a partner token, so a direct list never starts.
// The listing site itself splits the same document across two public reads:
// categories, brands and attributes, then the category size sets.
async function depopAttributes(deadline) {
  return await marketplaceCatalog('depop:attributes', () => marketplaceCatalogRead('depop', 'category and size list', async () => {
    const read = async path => {
      const { data } = await marketplaceFetch(`${DEPOP_WEBAPI}${path}`, {
        marketplace: 'depop', step: 'catalog', credentials: 'omit',
        headers: { Accept: 'application/json' }, deadline, timeoutMs: 30000,
      });
      return data && typeof data === 'object' ? data : {};
    };
    const [attributes, sizes] = await Promise.all([
      read('/presentation/api/v1/attributes/'),
      read('/presentation/api/v1/attributes/categories/size-mapping/'),
    ]);
    const data = { ...attributes, category_size_mapping: sizes.category_size_mapping, size_sets: sizes.size_sets };
    if (!['department', 'group', 'size_sets', 'category_size_mapping', 'generic_attributes'].every(key => Array.isArray(data[key]))) {
      throw marketplaceError('Depop\'s category list changed.', { code: 'schema_drift' });
    }
    return data;
  }));
}

async function depopBearer() {
  const cookie = await chrome.cookies.get({ url: 'https://www.depop.com/', name: 'access_token' });
  if (!cookie?.value) throw marketplaceError('Sign in to Depop in Chrome first. No tab will be opened automatically.', { code: 'signed_out' });
  return `Bearer ${decodeURIComponent(cookie.value)}`;
}

// One signed-in read for the seller's own shipping options.
async function depopAccountRead(path, what, deadline) {
  return await marketplaceCatalogRead('depop', what, async () => {
    const { data } = await marketplaceFetch(`${DEPOP_WEBAPI}${path}`, { marketplace: 'depop', step: 'catalog', credentials: 'omit',
      headers: { Accept: 'application/json', Authorization: await depopBearer() }, deadline });
    return data;
  }, { signedIn: true });
}

function depopProduct(attributes, department, productType, leaf) {
  if (!department || !productType || !attributes.department?.some(entry => entry.id === department)) return null;
  const group = (attributes.group || []).find(entry => (!Array.isArray(entry.department) || entry.department.includes(department))
    && (entry.product_types || []).some(type => type.id === productType));
  if (!group) return null;
  const type = group.product_types.find(entry => entry.id === productType);
  return { leaf: leaf || `${department}_${productType}`, department, group: group.id, productType, attributeIds: type.attribute_ids || [] };
}

// Depop's own product type for a direct id such as menswear_tshirts.
function depopFromDirect(listing, attributes) {
  const match = /^(kidswear|menswear|womenswear|everything-else)_(.+)$/.exec(String(listing.direct_category_ids?.depop || ''));
  return match ? depopProduct(attributes, match[1], match[2], match[0]) : null;
}

// The one live product type whose name matches the listing's category path.
function depopByName(listing, attributes) {
  const object = listing.marketplace_category_objects?.depop;
  const text = Array.isArray(object?.displayPath) ? object.displayPath.join(' > ') : String(listing.marketplace_categories?.depop || '');
  const parts = text.split('>').map(part => part.trim()).filter(Boolean);
  const leaf = marketplaceNameKey(parts[parts.length - 1] || '');
  const head = marketplaceNameKey(parts[0] || '');
  const department = /kid/.test(head) ? 'kidswear' : /women|female/.test(head) ? 'womenswear' : /men|male/.test(head) ? 'menswear' : '';
  if (!leaf) return null;
  const hits = [];
  for (const group of attributes.group || []) {
    for (const type of group.product_types || []) {
      if (marketplaceNameKey(type.id) !== leaf && marketplaceNameKey(depopName(type)) !== leaf) continue;
      for (const id of (Array.isArray(group.department) ? group.department : [])) {
        if (!department || id === department) hits.push(depopProduct(attributes, id, type.id));
      }
    }
  }
  const found = hits.filter(Boolean);
  return found.length === 1 ? found[0] : null;
}

// The leaf's department and product type. A direct category id is Depop's own.
// A live Vendoo hit names the product type in its extras; a tree leaf is
// ``<department>_<product type>`` under a ``<department>_<group>`` parent,
// checked against Depop's own list. A stale id falls back to the path's name.
function depopCategory(listing, attributes) {
  const direct = depopFromDirect(listing, attributes);
  if (direct) return direct;
  const resolved = listing.form?.overrides?.categoryV2 || listing.marketplace_category_objects?.depop;
  const leaf = String(resolved?.id || '');
  const chain = Array.isArray(resolved?.path) ? resolved.path.map(String) : [];
  const department = String(chain[0] || leaf.split('_')[0] || '');
  const parent = chain.length > 2 ? chain[chain.length - 2] : '';
  const candidates = [resolved?.extras?.productType, parent && leaf.startsWith(`${parent}_`) ? leaf.slice(parent.length + 1) : '',
    leaf.startsWith(`${department}_`) ? leaf.slice(department.length + 1) : ''].filter(Boolean).map(String);
  for (const productType of candidates) {
    const hit = depopProduct(attributes, department, productType, leaf);
    if (hit) return hit;
  }
  return depopByName(listing, attributes);
}

// The US size set for the product type and the listing's size inside it.
function depopSize(listing, category, attributes) {
  const mapping = attributes.category_size_mapping.find(entry => entry.department === category.department && entry.product_type === category.productType);
  const setId = mapping?.size_set_by_region?.US;
  if (setId == null) return { none: true };
  const set = attributes.size_sets.find(entry => String(entry.id) === String(setId));
  if (!set) return { problem: 'a size Depop offers for this category' };
  const sizes = set.sizes || [];
  const coded = String(listing.form?.aspects?.[`${category.leaf}_size`] ?? '').trim();
  if (coded && sizes.some(size => String(size.id) === coded)) return { set: Number(set.id), id: Number(coded) };
  const wanted = String(listing.size || '').trim();
  if (!wanted) return { problem: 'a size' };
  const found = matchSize(wanted, sizes.map(size => ({ size, keys: sizeEntryKeys([depopName(size)]) })));
  return found.length ? { set: Number(set.id), id: Number(found[0].size.id) } : { problem: 'a size Depop offers for this category' };
}

// Depop's brand id for this listing. An unknown or blank brand is Other.
function depopBrand(listing, attributes) {
  const brands = Array.isArray(attributes?.brand) ? attributes.brand : attributes?.brands;
  const wanted = listing?.depop_specifics?.brand || listing?.form?.overrides?.brand || listing?.brand;
  return depopBrandId(brands, wanted);
}

const DEPOP_FIT_ATTRIBUTE = { petite: 'petite', plus: 'plussize', 'big_&_tall': 'tall', maternity: 'maternity' };

// Material, occasion and body fit as Depop's attribute value ids, limited to
// the attributes this product type has and to each one's maximum.
function depopAttributeValues(listing, category, attributes) {
  const specifics = listing.depop_specifics || {};
  const asList = value => (Array.isArray(value) ? value : String(value || '').split(',')).map(item => String(item).trim()).filter(Boolean);
  const fit = DEPOP_FIT_ATTRIBUTE[listingSizeFit(listing)];
  const wanted = { material: asList(specifics.material), occasion: asList(specifics.occasion), 'body-fit': fit ? [fit] : [] };
  const values = {};
  const missing = [];
  for (const id of category.attributeIds) {
    const attribute = attributes.generic_attributes.find(entry => entry.id === id);
    if (!attribute) continue;
    const options = (attribute.attributes || []).filter(option => option.status !== 'inactive');
    const ids = [...new Set((wanted[id] || []).map(label => options.find(option => marketplaceNameKey(depopName(option)) === marketplaceNameKey(label)
      || marketplaceNameKey(option.id) === marketplaceNameKey(label))?.id).filter(Boolean))];
    const chosen = ids.slice(0, Math.max(1, Number(attribute.max_selection) || 1));
    if (chosen.length) values[id] = chosen;
    else if (attribute.is_mandatory) missing.push({ field: id, needs: `a ${depopName(attribute).toLowerCase() || id}` });
  }
  return { values, missing };
}

// The seller's ship-from address. Depop can store several at the same ZIP and
// does not mark one as the default, so the most recently updated match is used.
function depopAddress(listing, data) {
  const raw = Array.isArray(data) ? data : data?.addresses || data?.objects || data?.data || [];
  const rows = (Array.isArray(raw) ? raw : []).filter(entry => entry?.id != null);
  const zip = String(listing.form?.ships_from_zip || '').replace(/\D/g, '').slice(0, 5);
  const postal = entry => String(entry?.postal_code ?? entry?.postalCode ?? entry?.zip ?? '').replace(/\D/g, '').slice(0, 5);
  const newest = group => group.slice().sort((a, b) => String(b.lastModified || b.last_modified || '').localeCompare(String(a.lastModified || a.last_modified || '')))[0];
  const matches = zip ? rows.filter(entry => postal(entry) === zip) : [];
  const chosen = matches.length ? newest(matches)
    : rows.length === 1 ? rows[0]
    : rows.find(entry => entry?.is_default === true || entry?.default === true || entry?.primary === true);
  return chosen?.id != null ? chosen.id : null;
}

// Every Depop id the draft needs, read before the draft is created.
// Needs a live check: the draft's own key names for department, group, size
// (variant_set + variants) and each shipping_methods entry.
async function depopResolvedFields(payload) {
  const listing = payload.listing || {};
  const missing = [];
  const base = depopBaseDescription(listing);
  if (!base) missing.push({ field: 'description', needs: 'a description' });
  if (base.length > DEPOP_MAX_DESCRIPTION) missing.push({ field: 'description', needs: `a title and description of ${DEPOP_MAX_DESCRIPTION} characters or fewer together` });
  if (/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/.test(base)) missing.push({ field: 'description', needs: 'a description without an email address' });
  if (!(Number(listing.price) >= 1)) missing.push({ field: 'price_amount', needs: 'a price of $1 or more' });
  if (!depopCondition(listing.form?.overrides?.condition || listing.condition)) missing.push({ field: 'condition', needs: 'a condition' });
  const attributes = await depopAttributes(payload.deadline);
  const category = depopCategory(listing, attributes);
  const fields = {};
  if (!category) missing.push({ field: 'product_type', needs: 'a Depop category' });
  else {
    Object.assign(fields, { department: category.department, group: category.group, product_type: category.productType,
      is_kids: category.department === 'kidswear' });
    const size = depopSize(listing, category, attributes);
    if (size.problem) missing.push({ field: 'size', needs: size.problem });
    else if (!size.none) Object.assign(fields, { variant_set: size.set, variants: { [size.id]: 1 } });
    const extra = depopAttributeValues(listing, category, attributes);
    if (Object.keys(extra.values).length) fields.attributes = extra.values;
    missing.push(...extra.missing);
  }
  fields.brand = depopBrand(listing, attributes);
  const parcelLabel = String(listing.depop_specifics?.parcelSize || listing.depop_specifics?.parcel_size || '').trim();
  if (!parcelLabel) missing.push({ field: 'parcel_size', needs: 'a parcel size' });
  if (missing.length) throw marketplaceMissing('depop', missing);
  const addressId = depopAddress(listing, await depopAccountRead('/api/v1/addresses/?providers=USPS', 'ship-from addresses', payload.deadline));
  const providers = await depopAccountRead(`/api/v1/shipping-providers/?ids=USPS&itemCurrency=USD&itemCost=${encodeURIComponent(String(listing.price))}`,
    'parcel sizes', payload.deadline);
  const parcels = (Array.isArray(providers) ? providers : providers?.objects || []).flatMap(provider => provider?.parcelSizes || provider?.parcel_sizes || []);
  const parcel = parcels.find(entry => marketplaceNameKey(entry?.title) === marketplaceNameKey(parcelLabel));
  if (addressId == null) {
    const zip = String(listing.form?.ships_from_zip || '').replace(/\D/g, '').slice(0, 5);
    missing.push({ field: 'address', needs: zip
      ? `a Depop ship-from address for ZIP ${zip} (the ZIP in Settings → Listings does not match an address saved in Depop)`
      : 'a ship-from address (set your Ships from ZIP in Settings → Listings)' });
  }
  if (!parcel?.id) missing.push({ field: 'parcel_size', needs: `a parcel size Depop offers (it has no "${parcelLabel}")` });
  if (missing.length) throw marketplaceMissing('depop', missing);
  fields.shipping_methods = [{ ship_from_address_id: addressId, shipping_provider_id: 'USPS', parcel_size_id: String(parcel.id), payer: 'buyer' }];
  return fields;
}

function depopDraftMatches(draft, listing) {
  return marketplaceText(draft.description) === marketplaceText(depopDescription(listing))
    && (listing.price == null || Number(draft.price_amount ?? draft.price) === Number(listing.price));
}

// One Depop step. Returns its result, or { error, safe_to_retry, ... } where
// safe_to_retry means this step wrote nothing.
async function depopApiAction(action, payload) {
  let wrote = false;
  try {
    const request = async (method, path, body, extraHeaders) => {
      const allowedRead = method === 'GET' && (/^\/presentation\/api\/v1\/drafts\/[A-Za-z0-9_-]{1,80}\/$/.test(path)
        || path === '/presentation/api/v1/drafts/?limit=24&sort_by=createdAt'
        || /^\/api\/v3\/drafts\/products\/[A-Za-z0-9_-]+\/status\/$/.test(path)
        || path === '/presentation/api/v1/attributes/'
        || path === '/presentation/api/v1/users/me/'
        || path === '/presentation/api/v1/addresses/'
        || path === '/presentation/api/v1/users/me/settings/shipping/'
        || /^\/presentation\/api\/v1\/shipping\/providers\/depop\/\?[A-Za-z0-9._~%=&+-]+$/.test(path));
      const draftWrite = payload.action === 'save_draft' && (
        (action === 'create' && method === 'POST' && path === '/presentation/api/v1/drafts/')
        || (action === 'photo' && method === 'POST' && path === '/presentation/api/v1/pictures/')
        || (action === 'save' && method === 'PUT' && /^\/presentation\/api\/v1\/drafts\/[A-Za-z0-9_-]{1,80}\/$/.test(path)));
      const publishWrite = action === 'publish' && payload.action === 'publish' && payload.confirm === true
        && method === 'POST' && path === '/presentation/api/v1/drafts/products/';
      if (!allowedRead && !draftWrite && !publishWrite) throw marketplaceError('Unsupported Depop action.');
      const headers = { Accept: 'application/json', Authorization: await depopBearer(), ...extraHeaders };
      if (method !== 'GET') {
        headers['Content-Type'] = 'application/json';
        if (publishWrite) headers['Depop-Session-Id'] = '';
      }
      // A signed photo slot only reserves an upload address, so it may be asked for again.
      const retry = method === 'GET' || action === 'photo';
      try {
        const { data } = await marketplaceFetch(`https://webapi.depop.com${path}`, { marketplace: 'depop', step: action, method, headers,
          credentials: 'omit', deadline: payload.deadline, retry, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
        if (method !== 'GET' && !retry) wrote = true;
        return data;
      } catch (error) {
        if (method !== 'GET' && !retry && error.rejected !== true) wrote = true;
        throw error;
      }
    };
    const id = String(payload.draft_url || '').match(/\/sellinghub\/drafts\/edit\/([A-Za-z0-9_-]{1,80})\/?$/)?.[1];
    const path = `/presentation/api/v1/drafts/${id}/`;
    const description = depopDescription(payload.listing);
    const summary = draft => ({ id: String(draft.id), description: draft.description, price: draft.price_amount,
      photo_count: draft.pictures?.length || 0, missing_fields: draft.missing_fields || [] });
    if (action === 'session') {
      await request('GET', '/presentation/api/v1/drafts/?limit=24&sort_by=createdAt');
      return { authenticated: true };
    }
    if (action === 'find') {
      const drafts = await request('GET', '/presentation/api/v1/drafts/?limit=24&sort_by=createdAt');
      const found = (drafts.objects || []).filter(draft => depopDraftMatches(draft, payload.listing));
      if (found.length !== 1 || !/^[A-Za-z0-9_-]{1,80}$/.test(String(found[0].id))) throw new Error('Depop could not identify this draft uniquely. Review drafts in Chrome.');
      return { ...summary(found[0]), draft_url: `https://www.depop.com/sellinghub/drafts/edit/${found[0].id}/` };
    }
    if (action === 'create') {
      const draft = await request('POST', '/presentation/api/v1/drafts/', {
        description, price_amount: String(payload.listing.price ?? ''), price_currency: 'USD', pictures: [], quantity: 1,
      });
      if (!/^[A-Za-z0-9_-]{1,80}$/.test(String(draft.id))) throw marketplaceError('Depop did not confirm its new draft ID. Check drafts before trying again.', { code: 'schema_drift', rejected: false });
      return { draft_url: `https://www.depop.com/sellinghub/drafts/edit/${draft.id}/` };
    }
    if (!id && action !== 'publish_status') throw marketplaceError('A saved Depop draft link is required.');
    if (action === 'read') return summary(await request('GET', path));
    if (action === 'photo') {
      const signed = await request('POST', '/presentation/api/v1/pictures/', {
        type: 'product', extension: 'jpg', dimensions: { width: 1280, height: 1280 },
      });
      let destination = null;
      try { destination = new URL(signed.url); } catch {}
      if (destination?.protocol !== 'https:' || !DEPOP_PHOTO_HOSTS.some(suffix => destination.hostname.endsWith(suffix)) || !/^\d+$/.test(String(signed.id))) {
        throw marketplaceError('Depop did not return a photo upload address Anteroom recognises.', { code: 'schema_drift' });
      }
      const bytes = Uint8Array.from(atob(payload.photo.base64), char => char.charCodeAt(0));
      const image = await createImageBitmap(new Blob([bytes], { type: payload.photo.mime_type }));
      const canvas = new OffscreenCanvas(1280, 1280);
      const context = canvas.getContext('2d');
      context.fillStyle = '#ffffff';
      context.fillRect(0, 0, 1280, 1280);
      const scale = Math.min(1280 / image.width, 1280 / image.height);
      context.drawImage(image, (1280 - image.width * scale) / 2, (1280 - image.height * scale) / 2, image.width * scale, image.height * scale);
      image.close();
      const file = await canvas.convertToBlob({ type: 'image/jpeg', quality: 0.95 });
      // A presigned PUT replaces the same object, so it is safe to repeat.
      await marketplaceFetch(destination.href, { marketplace: 'depop', step: 'photo-upload', method: 'PUT', credentials: 'omit',
        headers: { 'Content-Type': 'image/jpeg' }, body: file, deadline: payload.deadline, retry: true, expect: 'none' })
        .catch(error => { throw marketplaceStorageRefusal('depop', error); });
      return { id: signed.id };
    }
    if (action === 'save') {
      const existing = await request('GET', path);
      const draft = {};
      for (const key of ['address', 'age', 'attributes', 'brand', 'colour', 'condition', 'country', 'country_code',
        'gender', 'geo_lat', 'geo_lng', 'international_shipping_cost', 'is_kids', 'national_shipping_cost',
        'price_currency', 'product_type', 'quantity', 'repop_metadata', 'shipping_methods', 'sku', 'source', 'style', 'variants', 'videos']) {
        if (existing[key] != null) draft[key] = existing[key];
      }
      if (existing.variant_set_id != null) draft.variant_set = existing.variant_set_id;
      draft.description = description;
      draft.price_amount = String(payload.listing.price ?? existing.price_amount ?? '');
      draft.price_currency ||= 'USD';
      draft.pictures = payload.uploaded_photo_ids || (existing.pictures || []).map(photo => photo.id);
      Object.assign(draft, depopFormFields(payload.listing), payload.depop_fields || {});
      // A direct save has no precomputed fields. Fill category, brand, location, and shipping here.
      if (!payload.depop_fields) Object.assign(draft, await depopListingFields(request, payload.listing, payload.deadline));
      // Depop takes a quantity or a size set, never both.
      if (draft.variant_set != null) delete draft.quantity;
      await request('PUT', path, draft);
      const saved = await request('GET', path);
      if (!depopDraftMatches(saved, payload.listing) || saved.pictures?.length !== draft.pictures.length) throw new Error('Depop did not keep this listing\'s fields and photos.');
      return summary(saved);
    }
    if (action === 'prepare_publish' || action === 'publish') {
      if (payload.action !== 'publish' || payload.confirm !== true) throw marketplaceError('Confirm listing on Depop.');
      const draft = await request('GET', path);
      if (!depopDraftMatches(draft, payload.listing)) throw marketplaceError('Depop\'s draft does not match this revision. List again.');
      const missing = [...(Array.isArray(draft.missing_fields) ? draft.missing_fields : []).map(String),
        ...(!draft.pictures?.length ? ['photos'] : []), ...(!(Number(draft.price_amount) > 0) ? ['price'] : [])];
      if (missing.length || !Array.isArray(draft.missing_fields)) {
        const named = Array.isArray(draft.missing_fields) ? draft.missing_fields.map(String).join(', ') : '';
        throw marketplaceMissing('depop', [...new Set(missing.length ? missing : ['details'])]
          .map(field => ({ field, needs: depopFieldNeeds(field) })), named ? `missing_fields: ${named}` : '');
      }
      if (action === 'prepare_publish') return { ok: true };
      const job = await request('POST', '/presentation/api/v1/drafts/products/', { draft_ids: [id] });
      if (!/^[A-Za-z0-9_-]+$/.test(String(job.id))) throw marketplaceError('Depop did not confirm the publish result. Check its result before trying again.', { code: 'schema_drift', rejected: false });
      return { publish_job_id: String(job.id) };
    }
    if (action === 'publish_status') {
      if (!/^[A-Za-z0-9_-]+$/.test(String(payload.publish_job_id))) throw marketplaceError('A Depop publish receipt is required.');
      const status = await request('GET', `/api/v3/drafts/products/${payload.publish_job_id}/status/`);
      return { complete: status.count_by_status?.COMPLETE === 1 && status.count_by_status?.PENDING === 0,
        pending: status.count_by_status?.PENDING > 0 };
    }
    throw marketplaceError('Unsupported Depop action.');
  } catch (error) {
    return marketplaceFailure(error, !wrote);
  }
}

function depopDraftGone(error) {
  return /could not find draft/i.test(`${error?.message || ''} ${error?.marketplace_message || ''}`);
}

async function depopAction(action, payload) {
  if (activeNativeOperation?.cancelled) throw marketplaceError('Marketplace action cancelled.', { code: 'network' });
  const result = await depopApiAction(action, payload);
  if (!result || result.error) {
    const error = marketplaceError(result?.error || 'Depop did not confirm the result.', { code: result?.error_code || 'uncertain',
      rejected: result?.safe_to_retry === true, marketplace_message: result?.marketplace_message, retry_after_s: result?.retry_after_s, fields: result?.fields });
    throw error;
  }
  return result;
}

async function runDepopNative(msg, recovery = false) {
  const payload = { ...msg.payload, deadline: Date.now() + 120000 };
  const key = `native_operation_${payload.operation_id}`;
  // Before the final save the draft has no photos, and a retry reuses it.
  let committed = recovery;
  let replacedDraft = false;
  try {
    const storeReceipt = async receipt => {
      const journal = (await chrome.storage.local.get(key))[key] || {};
      await chrome.storage.local.set({ [key]: { ...journal, ...receipt } });
    };
    const verify = async () => {
      const draft = await depopAction('read', payload);
      if (!depopDraftMatches(draft, payload.listing)
          || (payload.expected_photo_count != null && draft.photo_count !== payload.expected_photo_count)) {
        throw new Error('Depop did not keep this listing\'s fields and photos. Check them and list again.');
      }
      return { ok: true, state: 'draft', draft_url: payload.draft_url, ...(replacedDraft ? { draft_replaced: true } : {}) };
    };
    const openDraft = async () => {
      photosChosen();
      try {
        const created = await depopAction('create', payload);
        payload.draft_url = nativeDraftUrl('depop', created.draft_url);
      } catch (error) {
        // A create whose reply was lost may have made a draft Anteroom cannot name.
        if (error.rejected !== true) committed = true;
        throw error;
      }
      if (!payload.draft_url) { committed = true; throw new Error('Depop did not return a valid draft link.'); }
      payload.expected_photo_count = payload.photos.length;
      await storeReceipt({ receipt_url: payload.draft_url, expected_photo_count: payload.expected_photo_count });
    };
    const photosChosen = () => {
      if (!payload.photos?.length || payload.photos.length > 8) {
        throw marketplaceMissing('depop', [{ field: 'photos', needs: 'between one and eight photos' }]);
      }
    };
    if (payload.action === 'save_draft') {
      if (!recovery) payload.depop_fields = await depopResolvedFields(payload);
      if (recovery) {
        if (!payload.draft_url) {
          const found = await depopAction('find', payload);
          payload.draft_url = found.draft_url;
          await storeReceipt({ receipt_url: found.draft_url });
        }
        const draft = await depopAction('read', payload);
        if (!depopDraftMatches(draft, payload.listing)) throw new Error('Depop\'s listing does not match this revision. List again.');
        if (draft.photo_count === 0) return marketplaceFailure(new Error('Depop kept the listing before its photos were saved. List again to finish the same one.'), true, { draft_url: payload.draft_url });
        return await verify();
      }
      if (!payload.draft_url) await openDraft();
      let existing;
      try {
        existing = await depopAction('read', payload);
      } catch (error) {
        // The seller deleted this draft. Start another instead of listing the missing one.
        if (!depopDraftGone(error)) throw error;
        payload.draft_url = '';
        replacedDraft = true;
        await openDraft();
        existing = await depopAction('read', payload);
      }
      if (existing.photo_count === 0) {
        photosChosen();
        payload.expected_photo_count = payload.photos.length;
        await storeReceipt({ expected_photo_count: payload.expected_photo_count });
        payload.uploaded_photo_ids = [];
        for (const photo of payload.photos) {
          const uploaded = await depopAction('photo', { ...payload, photo: await nativePhoto(msg.job_id, photo, payload.deadline) });
          payload.uploaded_photo_ids.push(uploaded.id);
        }
      }
      try { await depopAction('save', payload); } catch (error) {
        if (error.rejected !== true) committed = true;
        throw error;
      }
      committed = true;
      return await verify();
    }
    if (!recovery) {
      await depopAction('prepare_publish', payload);
      try {
        const receipt = await depopAction('publish', payload);
        committed = true;
        payload.publish_job_id = receipt.publish_job_id;
        await storeReceipt(receipt);
      } catch (error) {
        if (error.rejected !== true) committed = true;
        throw error;
      }
    }
    if (!payload.publish_job_id) throw new Error('No confirmed Depop publish receipt is available. Review the native account.');
    while (Date.now() < payload.deadline) {
      const result = await depopAction('publish_status', payload);
      if (result.complete) return { ok: true, state: 'published', draft_url: payload.draft_url };
      if (!result.pending) throw new Error('Depop did not confirm the listing. Check Depop before listing again.');
      await marketplaceSleep(2000);
    }
    throw new Error('Depop has not confirmed publishing yet. Check its result later.');
  } catch (error) {
    if (depopDraftGone(error) && !replacedDraft) payload.draft_url = '';
    return marketplaceFailure(error, !committed, payload.draft_url ? { draft_url: payload.draft_url } : {});
  }
}
