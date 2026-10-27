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
function depopPrice(listing) {
  const locked = Array.isArray(listing?.seller_fields) && listing.seller_fields.includes('depop_specifics.price');
  const override = listing?.depop_specifics?.price;
  if (locked && override != null && override !== '') return override;
  return listing?.price;
}

// Anteroom sends the Depop form's own photos in order; Depop keeps eight.
function depopChosenPhotos(photos) {
  const list = Array.isArray(photos) ? photos : [];
  const capped = list.slice(0, 8);
  return { photos: capped, omitted: Math.max(0, list.length - capped.length) };
}

function depopFormFields(listing, attributes) {
  const specifics = listing?.form?.specifics || {};
  const fields = {};
  const colour = [listing?.primaryColor, listing?.secondaryColor].map(depopColour).filter(Boolean);
  if (colour.length) fields.colour = [...new Set(colour)].slice(0, 2);
  const condition = depopCondition(listing?.depop_specifics?.condition || listing?.form?.overrides?.condition || listing?.condition);
  if (condition) fields.condition = condition;
  for (const key of ['age', 'source', 'style']) {
    const own = listing?.depop_specifics?.[key];
    const definition = attributes?.[key];
    const raw = definition?.attributes ? own ?? specifics[key] : Array.isArray(own) ? own : specifics[key];
    let values = (Array.isArray(raw) ? raw : typeof raw === 'string' ? [raw] : []).map(String).filter(Boolean);
    if (definition?.attributes) {
      values = values.map(value => definition.attributes.find(row => row.status !== 'inactive'
        && (marketplaceNameKey(row.id) === marketplaceNameKey(value) || marketplaceNameKey(depopName(row)) === marketplaceNameKey(value)))?.id).filter(Boolean);
    }
    if (values.length || Array.isArray(own)) fields[key] = [...new Set(values)].slice(0, Number(definition?.max_selection) || 3);
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

// The seller's country as Depop's listing site stores it: name, code, and
// the country's own coordinates. Empty when Depop does not name a country.
async function depopCountryLocation(request, deadline) {
  const account = await request('GET', '/presentation/api/v1/users/me/');
  const code = String(account?.country || '').toUpperCase();
  if (!/^[A-Z]{2}$/.test(code)) return {};
  const { data } = await marketplaceFetch('https://assets.depop.com/web/assets/listing/location/countries.json', {
    marketplace: 'depop', step: 'location', credentials: 'omit', deadline, retry: true,
  });
  const country = (Array.isArray(data) ? data : []).find(row => row?.countryCode === code);
  if (!country?.address || !country.country || !Number.isFinite(Number(country.geoLat)) || !Number.isFinite(Number(country.geoLng))) return {};
  return { address: country.address, country: country.country, country_code: country.countryCode,
    geo_lat: Number(country.geoLat), geo_lng: Number(country.geoLng) };
}

// Category, brand, country-level location, and Depop Shipping. Each lookup is
// skipped when Depop does not answer, so publish can still name what is missing.
async function depopListingFields(request, listing, deadline) {
  const fields = {};
  const category = depopCategoryFields(listing);
  if (category) Object.assign(fields, category);
  try {
    const attributes = await request('GET', '/presentation/api/v1/attributes/');
    const brand = depopBrandId(attributes?.brand, depopBrandText(listing));
    if (brand) fields.brand = brand;
  } catch { /* The publish check names a missing brand. */ }
  try {
    Object.assign(fields, await depopCountryLocation(request, deadline));
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

const DEPOP_LOCATION_KEYS = ['address', 'country', 'country_code', 'geo_lat', 'geo_lng'];

// Depop has no title. The description is the only text buyers see.
const DEPOP_MAX_DESCRIPTION = 1000;
const DEPOP_MAX_HASHTAGS = 5;
const DEPOP_PHOTO_HOSTS = ['.s3.amazonaws.com', '.s3-accelerate.amazonaws.com'];
const DEPOP_WEBAPI = 'https://webapi.depop.com';

function depopBaseDescription(listing) {
  return String(marketplaceCopy(listing, 'depop', 'description') || '').trim();
}

// The description, then up to five of the listing's tags as hashtags,
// as many as fit Depop's limit. The title is not included.
function depopDescription(listing) {
  let text = depopBaseDescription(listing).slice(0, DEPOP_MAX_DESCRIPTION);
  const tags = listingTags(listing).map(tag => tag.replace(/[^A-Za-z0-9]+/g, '').toLowerCase()).filter(Boolean);
  const chosen = [...new Set(tags)].slice(0, DEPOP_MAX_HASHTAGS).map(tag => `#${tag}`);
  while (chosen.length && `${text}\n\n${chosen.join(' ')}`.length > DEPOP_MAX_DESCRIPTION) chosen.pop();
  if (chosen.length && !/#\w/.test(text)) text = `${text}\n\n${chosen.join(' ')}`;
  return text;
}

const depopName = entry => String(entry?.name_i18n?.['en-US'] ?? entry?.name_i18n?.en ?? entry?.name ?? entry?.title ?? entry?.id ?? '');

// Depop's taxonomy. The partner host api.depop.com/api/v3/attributes/ answers
// 403 "not authorized" without a partner token, so a direct list never starts.
// The listing site splits the same document across two reads: categories,
// brands and attributes, then the category size sets. Both refuse a request
// that does not carry the seller's session, and the form then has no choices.
async function depopAttributes(deadline) {
  return await marketplaceCatalog('depop:attributes', () => marketplaceCatalogRead('depop', 'category and size list', async () => {
    const authorization = await depopBearer();
    const read = async path => {
      const { data } = await marketplaceFetch(`${DEPOP_WEBAPI}${path}`, {
        marketplace: 'depop', step: 'catalog', credentials: 'omit',
        headers: { Accept: 'application/json', Authorization: authorization }, deadline, timeoutMs: 12000,
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
  }, { signedIn: true }));
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
  if (Object.hasOwn(listing.depop_specifics || {}, 'nativeSizeId')) {
    const id = String(listing.depop_specifics.nativeSizeId);
    return sizes.some(size => String(size.id) === id) ? { set: Number(set.id), id: Number(id) }
      : { problem: 'a size Depop offers for this category' };
  }
  const coded = String(listing.form?.aspects?.[`${category.leaf}_size`] ?? '').trim();
  if (coded && sizes.some(size => String(size.id) === coded)) return { set: Number(set.id), id: Number(coded) };
  // Anteroom writes Depop's own label (a kids' age such as "16 years") on the
  // Depop form; the tag size is the fallback. Other is never chosen for a tag.
  const wanted = [listing.depop_specifics?.size, listing.size].map(value => String(value || '').trim())
    .filter(value => value && value.toLowerCase() !== 'other');
  if (!wanted.length) return { problem: 'a size' };
  const entries = sizes.map(size => ({ size, keys: sizeEntryKeys([depopName(size)]) }));
  for (const label of wanted) {
    const found = matchSize(label, entries);
    if (found.length) return { set: Number(set.id), id: Number(found[0].size.id) };
  }
  return { problem: 'a size Depop offers for this category' };
}

// This form's brand, including a cleared one. Otherwise the imported override, then General.
function depopBrandText(listing) {
  const specifics = listing?.depop_specifics;
  if (specifics && typeof specifics === 'object' && !Array.isArray(specifics) && Object.hasOwn(specifics, 'brand')) {
    return specifics.brand ?? '';
  }
  return listing?.form?.overrides?.brand || listing?.brand || '';
}

// Depop's brand id for this listing. An unknown or blank brand is Other.
function depopBrand(listing, attributes) {
  const brands = Array.isArray(attributes?.brand) ? attributes.brand : attributes?.brands;
  return depopBrandId(brands, depopBrandText(listing));
}

const DEPOP_FIT_ATTRIBUTE = { petite: 'petite', plus: 'plussize', 'big_&_tall': 'tall', tall: 'tall', maternity: 'maternity' };

// Material, occasion, body fit and the product type's own type attribute
// (jacket-type, dress-type, …) as Depop's attribute value ids, limited to the
// attributes this product type has and to each one's maximum.
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
    const labels = Object.hasOwn(specifics.attributes || {}, id) ? asList(specifics.attributes[id])
      : wanted[id] || (/-type$/.test(id) ? asList(specifics.type) : []);
    const ids = [...new Set(labels.map(label => options.find(option => marketplaceNameKey(depopName(option)) === marketplaceNameKey(label)
      || marketplaceNameKey(option.id) === marketplaceNameKey(label))?.id).filter(Boolean))];
    const chosen = ids.slice(0, Math.max(1, Number(attribute.max_selection) || 1));
    if (chosen.length) values[id] = chosen;
    else if (attribute.is_mandatory) missing.push({ field: id, needs: `a ${depopName(attribute).toLowerCase() || id}` });
  }
  return { values, missing };
}

// The seller's ship-from address row. Depop can store several at the same ZIP
// and does not mark one as the default, so the most recently updated match is used.
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
  return chosen?.id != null ? chosen : null;
}

// Where the item is, from the ship-from address: its city and state, never the
// street, or the listing's own location. Coordinates only when Depop's row has
// them; otherwise save falls back to the country's.
function depopLocation(listing, row) {
  const text = value => String(value ?? '').trim();
  const city = text(row?.city ?? row?.town ?? row?.locality);
  const state = text(row?.state ?? row?.state_code ?? row?.stateCode ?? row?.region ?? row?.county);
  const address = city ? [city, state].filter(Boolean).join(', ') : text(listing?.depop_specifics?.location);
  const location = address ? { address } : {};
  const number = value => (value == null || value === '' ? NaN : Number(value));
  const lat = number(row?.geo_lat ?? row?.geoLat ?? row?.latitude ?? row?.lat);
  const lng = number(row?.geo_lng ?? row?.geoLng ?? row?.longitude ?? row?.lng);
  if (Number.isFinite(lat) && Number.isFinite(lng)) Object.assign(location, { geo_lat: lat, geo_lng: lng });
  return location;
}

// Every Depop id the draft needs, read before the draft is created.
// The draft stores product type, gender, variant_set plus variants, and a
// shipping method whose parcel field is parcel_size.
async function depopResolvedFields(payload) {
  const listing = payload.listing || {};
  const missing = [];
  const quantity = Number(listing.quantity ?? 1);
  const price = depopPrice(listing);
  const worldwide = listing.depop_specifics?.worldwideShipping;
  const internationalPrice = Number(worldwide?.price);
  if (worldwide?.enabled === true && (worldwide.price == null || worldwide.price === ''
    || !Number.isFinite(internationalPrice) || internationalPrice < 0 || internationalPrice >= 100
    || Math.abs(internationalPrice * 100 - Math.round(internationalPrice * 100)) > 0.000001)) {
    missing.push({ field: 'depop_specifics.worldwideShipping.price', needs: 'a worldwide shipping price from $0 to $99.99, in dollars and cents' });
  }
  if (!Number.isSafeInteger(quantity) || quantity < 1) missing.push({ field: 'quantity', needs: 'a positive whole-number quantity' });
  const base = depopBaseDescription(listing);
  if (!base) missing.push({ field: 'description', needs: 'a description' });
  if (base.length > DEPOP_MAX_DESCRIPTION) missing.push({ field: 'description', needs: `a description of ${DEPOP_MAX_DESCRIPTION} characters or fewer` });
  if (/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/.test(base)) missing.push({ field: 'description', needs: 'a description without an email address' });
  if (!(Number(price) >= 1)) missing.push({ field: 'price_amount', needs: 'a price of $1 or more' });
  if (!depopCondition(listing.depop_specifics?.condition || listing.form?.overrides?.condition || listing.condition)) missing.push({ field: 'condition', needs: 'a condition' });
  const attributes = await depopAttributes(payload.deadline);
  const category = depopCategory(listing, attributes);
  const fields = depopFormFields(listing, attributes);
    if (!category) missing.push({ field: 'product_type', needs: 'a Depop category' });
  else {
    // The listing site stores product type, kids, and gender. Department and
    // group are how the catalog is organised, not fields on the draft.
    const gender = category.department === 'kidswear'
      ? (/boy/i.test(String(listing.department || '')) ? 'male' : /girl/i.test(String(listing.department || '')) ? 'female' : 'unisex')
      : category.department === 'menswear' ? 'male' : category.department === 'womenswear' ? 'female' : '';
    Object.assign(fields, { product_type: category.productType, is_kids: category.department === 'kidswear',
      ...(gender ? { gender } : {}) });
    const size = depopSize(listing, category, attributes);
    if (size.problem) missing.push({ field: 'size', needs: size.problem });
    else if (!size.none) Object.assign(fields, { variant_set: size.set, variants: { [size.id]: quantity } });
    else Object.assign(fields, { variant_set: null, variants: {}, quantity });
    const extra = depopAttributeValues(listing, category, attributes);
    fields.attributes = extra.values;
    missing.push(...extra.missing);
  }
  fields.brand = depopBrand(listing, attributes);
  // Depop's listing site leaves sku off when it is blank, and so does Anteroom.
  const sku = String(listing.depop_specifics?.sku ?? listing.sku ?? '').trim();
  if (sku) fields.sku = sku;
  if (sku.length > 50) missing.push({ field: 'depop_specifics.sku', needs: 'a SKU of 50 characters or fewer' });
  fields.international_shipping_cost = worldwide?.enabled === true ? internationalPrice.toFixed(2) : null;
  const parcelLabel = String(listing.depop_specifics?.parcelSize || listing.depop_specifics?.parcel_size || '').trim();
  if (!parcelLabel) missing.push({ field: 'parcel_size', needs: 'a parcel size' });
  if (missing.length) throw marketplaceMissing('depop', missing);
  const from = depopAddress(listing, await depopAccountRead('/api/v1/addresses/?providers=USPS', 'ship-from addresses', payload.deadline));
  const providers = await depopAccountRead(`/api/v1/shipping-providers/?ids=USPS&itemCurrency=USD&itemCost=${encodeURIComponent(String(price))}`,
    'parcel sizes', payload.deadline);
  const parcels = (Array.isArray(providers) ? providers : providers?.objects || []).flatMap(provider => provider?.parcelSizes || provider?.parcel_sizes || []);
  const parcel = parcels.find(entry => marketplaceNameKey(entry?.title) === marketplaceNameKey(parcelLabel));
  if (!from) {
    const zip = String(listing.form?.ships_from_zip || '').replace(/\D/g, '').slice(0, 5);
    missing.push({ field: 'address', needs: zip
      ? `a Depop ship-from address for ZIP ${zip} (the ZIP in Settings → Listings does not match an address saved in Depop)`
      : 'a ship-from address (set your Ships from ZIP in Settings → Listings)' });
  }
  if (!parcel?.id) missing.push({ field: 'parcel_size', needs: `a parcel size Depop offers (it has no "${parcelLabel}")` });
  if (missing.length) throw marketplaceMissing('depop', missing);
  // The draft's shipping entry uses parcel_size. parcel_size_id is the partner
  // API's name, and Depop refuses a draft that carries it.
  fields.shipping_methods = [{
    payer: 'buyer', parcel_size: String(parcel.id), shipping_provider_id: 'USPS', ship_from_address_id: from.id,
  }];
  Object.assign(fields, depopLocation(listing, from));
  return fields;
}

// A quantity Depop stored as a number or as { quantity: n }.
function depopVariantQuantity(quantity) {
  if (quantity && typeof quantity === 'object') return depopVariantQuantity(quantity.quantity ?? quantity.value ?? quantity.qty);
  const number = Number(quantity);
  return Number.isFinite(number) ? number : NaN;
}

// Variant ids a draft holds: Depop stores { "<size id>": quantity }.
function depopVariantIds(variants) {
  if (Array.isArray(variants)) return variants.map(entry => String(entry?.id ?? entry?.variant_id ?? entry?.size_id ?? entry)).sort();
  if (!variants || typeof variants !== 'object') return [];
  return Object.entries(variants).filter(([, quantity]) => depopVariantQuantity(quantity) > 0).map(([id]) => String(id)).sort();
}

function depopAttributeMap(attributes) {
  if (Array.isArray(attributes)) {
    const map = {};
    for (const row of attributes) {
      if (!row || typeof row !== 'object') continue;
      const id = row.id ?? row.name;
      if (id == null || id === '') continue;
      map[String(id)] = row.values ?? row.value ?? row.attributes ?? [];
    }
    return map;
  }
  return attributes && typeof attributes === 'object' ? attributes : {};
}

// Depop's editor document is not one shape. The presentation draft can wrap
// the fields, name them in camelCase, or leave size and photos on the product
// the partner API uses. Reading only the top-level snake_case keys makes a
// saved draft look empty after a successful write.
function depopDraftDocument(payload) {
  if (Array.isArray(payload) || !payload || typeof payload !== 'object') return {};
  const wrappers = [payload.draft, payload.data, payload.object, payload.listing, payload.product, payload.item];
  const nested = wrappers.find(value => value && typeof value === 'object' && !Array.isArray(value)
    && (value.product_type != null || value.productType != null || value.pictures || value.pictureIds
      || value.picture_ids || value.media || value.description));
  const bare = payload.product_type == null && payload.productType == null && !payload.pictures && !payload.pictureIds && !payload.picture_ids;
  const source = nested && bare ? { ...payload, ...nested } : { ...payload };
  const category = source.category && typeof source.category === 'object' ? source.category : null;
  if (category) {
    if (source.product_type == null && source.productType == null) source.productType = category.productType ?? category.product_type;
    if (source.variant_set_id == null && source.variantSetId == null && source.variant_set == null) {
      source.variantSetId = category.variantSetId ?? category.variant_set_id ?? category.sizeSetId ?? category.size_set_id;
    }
    if (source.variants == null) source.variants = category.variants;
    if (source.gender == null) source.gender = category.gender;
    if (source.is_kids == null && source.isKids == null) source.isKids = category.isKids ?? category.is_kids;
  }
  const media = source.media && typeof source.media === 'object' ? source.media : null;
  if (!Array.isArray(source.pictures) && !Array.isArray(source.pictureIds) && !Array.isArray(source.picture_ids)) {
    const rows = media?.pictures || media?.photos || media?.images || source.images;
    if (Array.isArray(rows)) source.pictures = rows;
  }
  if (source.variants == null && (source.size_id != null || source.sizeId != null)) {
    source.variants = { [String(source.size_id ?? source.sizeId)]: source.quantity ?? 1 };
  }
  if (source.variant_set_id == null && source.variantSetId == null && source.variant_set == null
      && (source.size_set_id != null || source.sizeSetId != null)) {
    source.variantSetId = source.size_set_id ?? source.sizeSetId;
  }
  if (source.brand == null) source.brand = source.brand_id ?? source.brandId ?? source.brand_name ?? source.brandName;
  if (source.price_amount == null && source.priceAmount == null && source.price && typeof source.price === 'object') {
    source.priceAmount = source.price.priceAmount ?? source.price.price_amount ?? source.price.amount ?? null;
  }
  if (source.shipping_methods == null && Array.isArray(source.shippingMethods)) source.shipping_methods = source.shippingMethods;
  if (source.national_shipping_cost == null && source.nationalShippingCost != null) source.national_shipping_cost = source.nationalShippingCost;
  if (!Object.hasOwn(source, 'international_shipping_cost') && Object.hasOwn(source, 'internationalShippingCost')) {
    source.international_shipping_cost = source.internationalShippingCost;
  }
  return source;
}

// Picture ids in upload order. New drafts stay within Depop's eight. A live
// listing passes no cap so photos already on it are kept. Strings, numbers, and { id } all count.
function depopPictureRows(draftOrRows) {
  if (Array.isArray(draftOrRows)) return draftOrRows;
  const draft = depopDraftDocument(draftOrRows);
  for (const key of ['pictures', 'picture_ids', 'pictureIds', 'photos']) {
    if (Array.isArray(draft[key])) return draft[key];
  }
  return [];
}

function depopPictureIds(draftOrRows, limit = 8) {
  const ids = [];
  const cap = Number(limit);
  const limited = Number.isFinite(cap) && cap > 0;
  for (const row of depopPictureRows(draftOrRows)) {
    const raw = row && typeof row === 'object' ? (row.id ?? row.picture_id ?? row.pictureId) : row;
    const id = Number(raw);
    if (Number.isInteger(id) && id > 0 && !ids.includes(id)) ids.push(id);
    if (limited && ids.length === cap) break;
  }
  return ids;
}

// A picture row Depop returned with only a URL still counts. Numeric ids are
// what a write sends; a read-back often wraps those ids in formats.
function depopPictureCount(draftOrRows) {
  const ids = depopPictureIds(draftOrRows, null);
  return ids.length || depopPictureRows(draftOrRows).length;
}

function depopPhotosKept(saved, sentIds) {
  const wanted = depopPictureIds(sentIds);
  const kept = depopPictureIds(saved, null);
  if (kept.length) return kept.length === wanted.length && [...kept].sort((a, b) => a - b).join() === [...wanted].sort((a, b) => a - b).join();
  return depopPictureRows(saved).length === wanted.length;
}

function depopFieldId(value) {
  if (value == null || value === '') return '';
  if (typeof value === 'object') {
    const id = value.id ?? value.brand_id ?? value.brandId ?? value.picture_id ?? value.pictureId
      ?? value.variant_set_id ?? value.variantSetId ?? value.value_id ?? value.valueId;
    return id == null || typeof id === 'object' ? '' : String(id);
  }
  return String(value);
}

function depopAttributeIds(values) {
  return (Array.isArray(values) ? values : values == null || values === '' ? [] : [values])
    .map(value => {
      if (value && typeof value === 'object') {
        const id = value.id ?? value.value_id ?? value.valueId;
        if (id != null && id !== '') return String(id);
        const name = value.name ?? value.label ?? value.value;
        return name == null ? '' : String(name);
      }
      return String(value);
    })
    .filter(value => value !== '')
    .sort();
}

function depopSameList(left, right) {
  const key = values => depopAttributeIds(values).map(marketplaceNameKey).sort().join();
  return key(left) === key(right);
}

// Depop's draft read uses the names the form shows (objects, camelCase, value
// lists). Comparison uses the ids a write sent.
function depopDraftView(draft) {
  const source = depopDraftDocument(draft);
  const product = source.product_type ?? source.productType ?? source.category?.product_type ?? source.category?.productType ?? '';
  const set = source.variant_set_id ?? source.variantSetId ?? source.variant_set ?? source.variantSet;
  const conditionRaw = source.condition ?? source.condition_id ?? source.conditionId;
  const conditionId = depopFieldId(conditionRaw);
  const conditionName = conditionRaw && typeof conditionRaw === 'object' ? (conditionRaw.name || conditionRaw.label || '') : conditionRaw;
  const brand = source.brand ?? source.brand_id ?? source.brandId;
  return {
    product_type: product && typeof product === 'object' ? depopFieldId(product) : String(product || ''),
    variant_set: set == null || set === '' ? '' : (typeof set === 'object' ? depopFieldId(set) : String(set)),
    variants: source.variants ?? source.sizes,
    attributes: source.attributes,
    age: source.age, source: source.source, style: source.style,
    colour: source.colour ?? source.color ?? source.colours ?? source.colors,
    condition: depopCondition(conditionId) || depopCondition(conditionName) || conditionId,
    brand,
    sku: source.sku ?? '',
    quantity: source.quantity,
    international_shipping_cost: source.international_shipping_cost ?? source.internationalShippingCost,
    description: source.description,
    price: source.price_amount ?? source.priceAmount ?? source.price,
    shipping_methods: source.shipping_methods,
    measurements: source.measurements,
  };
}

function depopBrandToken(value) {
  if (value && typeof value === 'object') {
    const id = depopFieldId(value);
    if (id) return marketplaceNameKey(id);
    return marketplaceNameKey(value.name ?? value.title ?? value.label ?? '');
  }
  return marketplaceNameKey(value);
}

function depopRequestAttributes(attributes) {
  const map = depopAttributeMap(attributes);
  const out = {};
  for (const [id, values] of Object.entries(map)) {
    const ids = depopAttributeIds(values).filter(value => value !== 'undefined');
    if (id) out[id] = ids;
  }
  return out;
}

function depopRequestVariants(variants) {
  if (Array.isArray(variants)) {
    const out = {};
    for (const entry of variants) {
      const id = depopFieldId(entry);
      const quantity = depopVariantQuantity(entry?.quantity ?? entry?.value ?? entry);
      if (id && Number.isFinite(quantity)) out[id] = quantity;
    }
    return out;
  }
  if (!variants || typeof variants !== 'object') return variants;
  const out = {};
  for (const [id, quantity] of Object.entries(variants)) {
    const number = depopVariantQuantity(quantity);
    if (Number.isFinite(number)) out[id] = number;
  }
  return out;
}

function depopCleanShipping(methods) {
  if (!Array.isArray(methods)) return methods;
  return methods.map(method => {
    if (!method || typeof method !== 'object') return method;
    const next = {
      payer: method.payer,
      parcel_size: method.parcel_size != null ? String(method.parcel_size)
        : method.parcelSize != null ? String(method.parcelSize)
        : (method.parcel_size_id != null ? String(method.parcel_size_id) : undefined),
      shipping_provider_id: method.shipping_provider_id ?? method.shippingProviderId,
      ship_from_address_id: method.ship_from_address_id ?? method.shipFromAddressId,
    };
    return Object.fromEntries(Object.entries(next).filter(([, value]) => value != null));
  });
}

// The draft body Depop's own listing site sends (its web bundle's draft
// request builder): snake_case names only, pictures as uploaded ids, and sku,
// gender, price, quantity and shipping costs only when they have a value. An
// empty sku is never sent: from the first save that sent sku "" (#801), Depop
// answered 200 and kept nothing but the description and price.
// Response objects from a previous draft (brand records, videos, repop
// metadata) are not copied in.
function depopDraftWriteBody(listing, fields, pictures, existing) {
  const source = { ...depopFormFields(listing), ...(fields || {}) };
  const body = {
    description: depopDescription(listing),
    price_amount: String(depopPrice(listing) ?? ''),
    price_currency: 'USD',
    pictures: depopPictureIds(pictures),
  };
  for (const key of ['address', 'age', 'attributes', 'brand', 'colour', 'condition', 'country', 'country_code',
    'gender', 'geo_lat', 'geo_lng', 'international_shipping_cost', 'is_kids',
    'national_shipping_cost', 'product_type', 'quantity', 'shipping_methods', 'source', 'style', 'variants']) {
    if (Object.hasOwn(source, key) && source[key] != null) body[key] = source[key];
  }
  const sku = String(source.sku ?? '').trim();
  if (sku) body.sku = sku;
  if (body.brand && typeof body.brand === 'object') body.brand = depopFieldId(body.brand);
  if (body.condition && typeof body.condition === 'object') {
    body.condition = depopCondition(depopFieldId(body.condition)) || depopCondition(body.condition.name) || depopFieldId(body.condition);
  }
  if (body.attributes) body.attributes = depopRequestAttributes(body.attributes);
  if (body.variants) body.variants = depopRequestVariants(body.variants);
  if (Array.isArray(body.shipping_methods)) body.shipping_methods = depopCleanShipping(body.shipping_methods);
  for (const key of ['age', 'source', 'style', 'colour']) {
    if (Array.isArray(body[key])) body[key] = body[key].map(value => (value && typeof value === 'object' ? depopFieldId(value) : value)).filter(Boolean);
  }
  if (body.international_shipping_cost == null) delete body.international_shipping_cost;
  const prior = depopDraftDocument(existing);
  if (!Array.isArray(body.shipping_methods) && Array.isArray(prior.shipping_methods) && prior.shipping_methods.length) {
    body.shipping_methods = depopCleanShipping(prior.shipping_methods);
  }
  if (body.national_shipping_cost == null && prior.national_shipping_cost != null && typeof prior.national_shipping_cost !== 'object') {
    body.national_shipping_cost = String(prior.national_shipping_cost);
  }
  if (Object.hasOwn(source, 'variant_set') && source.variant_set != null) {
    body.variant_set = typeof source.variant_set === 'object' ? depopFieldId(source.variant_set) : source.variant_set;
    delete body.quantity;
  } else if (Object.hasOwn(source, 'variant_set')) {
    body.variants = {};
    if (source.quantity != null) body.quantity = source.quantity;
  }
  for (const key of ['gender', 'national_shipping_cost']) {
    if (!body[key]) delete body[key];
  }
  return body;
}

// The draft fields that differ from this revision: description and price, and
// when the resolved fields are known, the category, size and attributes sent.
function depopDraftMismatch(draft, listing, fields) {
  const view = depopDraftView(draft);
  const wrong = [];
  if (marketplaceText(view.description) !== marketplaceText(depopDescription(listing))) wrong.push('description');
  if (depopPrice(listing) != null && Number(view.price) !== Number(depopPrice(listing))) wrong.push('price');
  if (!fields) return wrong;
  if (fields.product_type != null && String(view.product_type) !== String(fields.product_type)) wrong.push('category');
  if (fields.variant_set != null) {
    if (String(view.variant_set) !== String(fields.variant_set)
      || depopVariantIds(view.variants).join() !== depopVariantIds(fields.variants).join()) wrong.push('size');
  } else if (Object.hasOwn(fields, 'variant_set') && (view.variant_set !== '' || depopVariantIds(view.variants).length)) {
    wrong.push('size');
  }
  if (Object.hasOwn(fields, 'attributes')) {
    const sent = depopAttributeMap(fields.attributes);
    const kept = depopAttributeMap(view.attributes);
    for (const id of new Set([...Object.keys(sent), ...Object.keys(kept)])) {
      if (!depopSameList(kept[id], sent[id])) wrong.push(id.replace(/-/g, ' '));
    }
  }
  for (const key of ['age', 'source', 'style', 'colour']) {
    if (Object.hasOwn(fields, key) && !depopSameList(view[key], fields[key])) wrong.push(key);
  }
  if (fields.condition != null && depopCondition(fields.condition) !== view.condition
    && marketplaceNameKey(fields.condition) !== marketplaceNameKey(view.condition)) wrong.push('condition');
  if (fields.brand != null && depopBrandToken(view.brand) !== depopBrandToken(fields.brand)) wrong.push('brand');
  if (fields.sku && String(view.sku || '') !== String(fields.sku)) wrong.push('SKU');
  if (Object.hasOwn(fields, 'international_shipping_cost')) {
    const actual = view.international_shipping_cost;
    if (fields.international_shipping_cost == null ? actual != null && actual !== ''
      : actual == null || actual === '' || Number(actual) !== Number(fields.international_shipping_cost)) wrong.push('worldwide shipping');
  }
  if (fields.variants && depopVariantIds(fields.variants).some(id => view.variants && typeof view.variants === 'object' && !Array.isArray(view.variants)
    && Object.hasOwn(view.variants, id) && depopVariantQuantity(view.variants[id]) !== depopVariantQuantity(fields.variants[id]))) wrong.push('quantity');
  if (fields.quantity != null && Number(view.quantity) !== fields.quantity) wrong.push('quantity');
  if (Array.isArray(fields.shipping_methods) && Array.isArray(view.shipping_methods)) {
    const parcel = methods => depopCleanShipping(methods).map(method => String(method?.parcel_size || '')).sort().join();
    if (parcel(fields.shipping_methods) !== parcel(view.shipping_methods)) wrong.push('shipping');
  }
  if (Array.isArray(fields.measurements) && Array.isArray(view.measurements)) {
    const norm = rows => rows.map(row => `${row?.id}:${String(row?.value ?? '').trim()}`).sort().join();
    if (norm(fields.measurements) !== norm(view.measurements)) wrong.push('measurements');
  }
  return wrong;
}

// Keys that hold a person's name, street address, phone, email, or exact
// position. Their values never reach a job error.
const DEPOP_PRIVATE_KEYS = /^(?:address\d?|address_?line_?\d|line_?\d|street\d?|city|town|province|state|county|postal_?code|post_?code|zip(?:_?code)?|phone(?:_?number)?|telephone|email|first_?name|last_?name|full_?name|recipient(?:_?name)?|name_on_address|geo_(?:position_)?(?:lat|lng)|latitude|longitude|lat|lng)$/i;

// A Depop body for a job error: signed URLs lose their query, and addresses,
// names, contact details, coordinates and tokens are hidden.
function depopSanitizedBody(value) {
  let json = '';
  try {
    json = typeof value === 'string' ? value : JSON.stringify(value, (key, item) => {
      if (key && DEPOP_PRIVATE_KEYS.test(key) && item != null && item !== '') return '[hidden]';
      if (typeof item === 'string' && item.includes('?')) {
        try {
          const url = new URL(item);
          if (url.search) return `${url.origin}${url.pathname}`;
        } catch { /* A field that is not a URL. */ }
      }
      return item;
    });
  } catch { json = ''; }
  return marketplaceScrub(json || '');
}

function depopDraftMatches(draft, listing, fields) {
  return depopDraftMismatch(draft, listing, fields).length === 0;
}

// Pictures Depop will accept on a write. A bare id list is rejected on a live
// listing (the product stores picture objects). Query strings are signatures.
function depopPictureWrite(source) {
  const written = [];
  for (const row of depopPictureRows(source)) {
    if (row && typeof row === 'object') {
      const url = row.url || row.formats?.P0?.url || row.formats?.P0?.src;
      if (typeof url === 'string' && url.startsWith('https://')) {
        try { written.push({ url: new URL(url).origin + new URL(url).pathname }); continue; } catch { /* Keep the id. */ }
      }
      const id = Number(row.id ?? row.picture_id ?? row.pictureId);
      if (Number.isInteger(id) && id > 0) written.push({ id });
    } else {
      const id = Number(row);
      if (Number.isInteger(id) && id > 0) written.push({ id });
    }
  }
  return written;
}

// Which fields the read-back actually contained. This stays at the front of
// the job error so a long body still names what came back.
function depopReadDigest(body) {
  const source = depopDraftDocument(body);
  const names = ['product_type', 'productType', 'variantSetId', 'variant_set', 'size_id', 'pictures', 'pictureIds',
    'colour', 'condition', 'brand', 'age', 'source', 'style', 'attributes', 'measurements', 'shipping_methods',
    'description', 'price_amount', 'priceAmount'];
  const present = names.filter(name => {
    const value = source[name];
    if (value == null || value === '') return false;
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === 'object') return Object.keys(value).length > 0;
    return true;
  });
  return `read-back keys: ${present.join(', ') || '(none)'}`;
}

// The live edit body Depop's own listing site sends for
// PUT /products/by-slug/<slug>/ (its web bundle's product request builder):
// picture_ids rather than picture objects, geo_position_lat/lng, the country
// code as country, attributes as an object, and no department, size_set_id,
// size_id, measurements or camelCase copies. Every field comes from the same
// resolution a draft save uses, so a category move carries the new category's
// size set. Only the photo ids are taken from the live product.
function depopLiveWriteBody(product, listing, fields) {
  const stored = Array.isArray(product?.picture_ids) ? product.picture_ids : product;
  const body = {
    age: fields.age,
    address: fields.address,
    attributes: depopRequestAttributes(fields.attributes || {}),
    brand: fields.brand,
    colour: fields.colour,
    condition: fields.condition,
    country: fields.country_code,
    description: depopDescription(listing),
    ...(fields.gender ? { gender: fields.gender } : {}),
    geo_position_lat: fields.geo_lat,
    geo_position_lng: fields.geo_lng,
    is_kids: fields.is_kids,
    picture_ids: depopPictureIds(stored, null),
    price_amount: String(depopPrice(listing) ?? ''),
    price_currency: 'USD',
    product_type: fields.product_type,
    shipping_methods: depopCleanShipping(fields.shipping_methods || []),
    ...(fields.sku ? { sku: fields.sku } : {}),
    source: fields.source,
    style: fields.style,
    variant_set: fields.variant_set ?? null,
    variants: depopRequestVariants(fields.variants || {}),
  };
  if (Object.hasOwn(fields, 'international_shipping_cost')) body.international_shipping_cost = fields.international_shipping_cost;
  if (typeof fields.quantity === 'number') body.quantity = fields.quantity;
  return Object.fromEntries(Object.entries(body).filter(([, value]) => value !== undefined));
}

// Depop's brand id on the live product. Its own debug view reads brand_id.
function depopProductBrand(product) {
  const id = product?.brand_id ?? depopFieldId(product?.brand);
  return id == null || id === '' ? '' : String(id);
}

// The product type a live product reports, wherever Depop put it.
function depopProductType(product) {
  const value = product?.attributes?.product_type ?? product?.product_type ?? product?.productType;
  return value == null || typeof value === 'object' ? '' : String(value);
}

// Waits before each read after a live edit: one read at once, then a few
// spaced retries while Depop's reads catch up with the write.
const DEPOP_LIVE_READ_WAITS_MS = [0, 1000, 2000, 4000];

// What a live edit sets, in the terms a read reports.
function depopLiveWanted(listing, fields) {
  const price = depopPrice(listing);
  const set = fields.variant_set;
  return { price: price == null || price === '' || !Number.isFinite(Number(price)) ? null : Number(price),
    product_type: fields.product_type ? String(fields.product_type) : '',
    variant_set: set == null || set === '' ? '' : String(set),
    sizes: depopVariantIds(fields.variants || {}) };
}

// What fresh reads of a live product say, taking each field from the first
// read that has it. A field no read names stays unknown: never a mismatch.
function depopLiveState(docs) {
  const state = { price: null, product_type: '', variant_set: '', sizes: null };
  for (const doc of docs) {
    const source = depopDraftDocument(doc);
    if (state.price == null) state.price = depopProductPrice(source);
    if (!state.product_type) state.product_type = depopProductType(source) || depopDraftView(doc).product_type;
    if (!state.variant_set) {
      const set = source.variant_set_id ?? source.variantSetId ?? source.variant_set;
      if (set != null && set !== '') state.variant_set = String(typeof set === 'object' ? depopFieldId(set) : set);
    }
    if (state.sizes == null) {
      const variants = Array.isArray(source.variants_all) ? source.variants_all : source.variants;
      const ids = variants == null ? [] : depopVariantIds(variants);
      if (ids.length) state.sizes = ids;
    }
  }
  return state;
}

// The fields a fresh read shows with a different value than the edit sent.
function depopLiveMismatch(state, wanted) {
  const parts = [];
  if (wanted.price != null && state.price != null && state.price !== wanted.price) parts.push('price');
  if (wanted.product_type && state.product_type && state.product_type !== wanted.product_type) parts.push('category');
  const setWrong = wanted.variant_set && state.variant_set && state.variant_set !== wanted.variant_set;
  const sizeWrong = wanted.sizes.length && state.sizes?.length && state.sizes.join(',') !== wanted.sizes.join(',');
  if (setWrong || sizeWrong) parts.push('size');
  return parts;
}

function depopLiveDigest(state) {
  return `price=${state.price ?? '?'} product_type=${state.product_type || '?'} variant_set=${state.variant_set || '?'} sizes=${state.sizes?.join('/') || '?'}`;
}

const DEPOP_PICTURE_CHECK = '/presentation/api/v1/pictures/validate/';

// Depop's listing site uploads a photo, then asks this endpoint until every id
// is valid and processing is complete (ten tries, 200 ms apart), and only then
// puts the ids on the draft. The valid ids come back in upload order.
async function depopProcessedPhotos(request, uploaded) {
  const ids = depopPictureIds(uploaded, null);
  let valid = [];
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const answer = await request('POST', DEPOP_PICTURE_CHECK, { picture_ids: ids });
    valid = (Array.isArray(answer?.valid_picture_ids) ? answer.valid_picture_ids : []).map(Number);
    if (valid.length === ids.length && answer?.status === 'complete') break;
    if (attempt < 9) await marketplaceSleep(200);
  }
  return ids.filter(id => valid.includes(id));
}

// The country name, code and coordinates a draft needs beside the city.
async function depopWithCountry(request, fields, deadline) {
  if (!DEPOP_LOCATION_KEYS.some(key => fields[key] == null)) return fields;
  let located = {};
  try { located = await depopCountryLocation(request, deadline); } catch { /* The publish check names a missing location. */ }
  const out = { ...fields };
  for (const key of DEPOP_LOCATION_KEYS) {
    if (out[key] == null && located[key] != null) out[key] = located[key];
  }
  return out;
}

// A live edit resolves the listing exactly as a draft save does, including
// the size from the new category's size set: a size outside it stops the edit
// before anything is sent. A brand Depop does not list keeps the product's
// own brand, so a move never retitles Vassarette as Other.
async function depopLiveFields(request, payload, product) {
  if (payload.depop_fields) return await depopWithCountry(request, payload.depop_fields, payload.deadline);
  const fields = await depopWithCountry(request, await depopResolvedFields(payload), payload.deadline);
  const attributes = await depopAttributes(payload.deadline);
  const other = depopOtherId(Array.isArray(attributes?.brand) ? attributes.brand : attributes?.brands || []);
  const listing = payload.listing || {};
  const wanted = String(depopBrandText(listing) || '').trim().toLowerCase();
  const stored = depopProductBrand(product);
  if (fields.brand === other && wanted && wanted !== 'other' && wanted !== 'unbranded' && stored) fields.brand = stored;
  return fields;
}

// One Depop step. Returns its result, or { error, safe_to_retry, ... } where
// safe_to_retry means this step wrote nothing.
async function depopApiAction(action, payload) {
  let wrote = false;
  try {
    const request = async (method, path, body, extraHeaders) => {
      const allowedRead = method === 'GET' && (/^\/presentation\/api\/v1\/drafts\/[A-Za-z0-9_-]{1,80}\/$/.test(path)
        || path === '/presentation/api/v1/drafts/?limit=24&sort_by=createdAt'
        || /^\/api\/v2\/drafts\/[A-Za-z0-9_-]{1,80}\/$/.test(path)
        || /^\/api\/v3\/drafts\/products\/[A-Za-z0-9_-]+\/status\/$/.test(path)
        || path === '/presentation/api/v1/attributes/'
        || path === '/presentation/api/v1/users/me/'
        || depopShopRead(path)
        || path === '/presentation/api/v1/addresses/'
        || path === '/presentation/api/v1/users/me/settings/shipping/'
        || /^\/presentation\/api\/v1\/shipping\/providers\/depop\/\?[A-Za-z0-9._~%=&+-]+$/.test(path));
      const draftWrite = payload.action === 'save_draft' && (
        (action === 'create' && method === 'POST' && path === '/presentation/api/v1/drafts/')
        || (action === 'photo' && method === 'POST' && path === '/presentation/api/v1/pictures/')
        || (action === 'save' && method === 'POST' && path === DEPOP_PICTURE_CHECK)
        || (action === 'save' && method === 'PUT' && /^\/presentation\/api\/v1\/drafts\/[A-Za-z0-9_-]{1,80}\/$/.test(path)));
      const publishWrite = action === 'publish' && payload.action === 'publish' && payload.confirm === true
        && method === 'POST' && path === '/presentation/api/v1/drafts/products/';
      // Live edit: read and PUT the product by the slug the seller opened.
      // PUT on the numeric presentation id answers 404.
      // The edit page itself reads by-slug/<slug>/edit-listing/, the owner's document with the price.
      const updateRead = action === 'update' && method === 'GET' && /^\/presentation\/api\/v1\/products\/(?:by-slug\/[a-z0-9][a-z0-9-]{0,200}\/(?:edit-listing\/)?|[0-9]+\/)$/i.test(path);
      const updateWrite = action === 'update' && payload.confirm === true && method === 'PUT'
        && /^\/presentation\/api\/v1\/products\/by-slug\/[a-z0-9][a-z0-9-]{0,200}\/$/i.test(path);
      if (!allowedRead && !draftWrite && !publishWrite && !updateRead && !updateWrite) throw marketplaceError('Unsupported Depop action.');
      const headers = { Accept: 'application/json', Authorization: await depopBearer(), ...extraHeaders };
      if (method !== 'GET') {
        headers['Content-Type'] = 'application/json';
        if (publishWrite) headers['Depop-Session-Id'] = '';
      }
      // A signed photo slot only reserves an upload address, so it may be asked for again.
      // Asking whether photos finished processing writes nothing either.
      const retry = method === 'GET' || action === 'photo' || path === DEPOP_PICTURE_CHECK;
      try {
        const { data } = await marketplaceFetch(`https://webapi.depop.com${path}`, { marketplace: 'depop', step: action, method, headers,
          credentials: 'omit', deadline: payload.deadline, retry, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
        if (method !== 'GET' && !retry) wrote = true;
        return data;
      } catch (error) {
        if (method !== 'GET' && !retry && error.rejected !== true) wrote = true;
        const detail = error?.response_data !== undefined ? depopSanitizedBody(error.response_data) : String(error?.response_body || '');
        // The field names a refused write carried, never their values, so the
        // next refusal can be compared with what Depop's own site sends.
        const sent = method !== 'GET' && body && typeof body === 'object' && error?.status >= 400 && error.status < 500
          ? `sent: ${Object.keys(body).sort().join(', ')}` : '';
        const extra = [detail && !String(error.marketplace_message || '').includes(detail) ? detail : '', sent].filter(Boolean);
        if (extra.length) error.marketplace_message = [error.marketplace_message, ...extra].filter(Boolean).join(' | ');
        if (detail || sent) marketplaceLog('depop', action, { method, status: error.status || null, error_code: error.code || null, body: detail, sent });
        throw error;
      }
    };
    const keptError = (parts, body, reply) => {
      const error = new Error(`Depop did not keep this listing's ${parts.join(', ')}.`);
      const answered = reply && typeof reply === 'object' && Object.keys(reply).length ? `write reply: ${depopSanitizedBody(reply)}` : '';
      const detail = [depopReadDigest(body), answered, depopSanitizedBody(body)].filter(Boolean).join(' ');
      if (detail) error.marketplace_message = detail;
      marketplaceLog('depop', 'read-back', { body: detail || null });
      return error;
    };
    const readDraft = async () => {
      const primary = await request('GET', path);
      if (!payload.depop_fields) return primary;
      const score = draft => depopDraftMismatch(draft, payload.listing, payload.depop_fields).length
        + (payload.expected_photo_count != null && depopPictureCount(draft) !== payload.expected_photo_count ? 1 : 0);
      if (!score(primary)) return primary;
      // The presentation document can be a summary. The editor document is on v2.
      try {
        const fuller = await request('GET', `/api/v2/drafts/${id}/`);
        if (score(fuller) < score(primary)) return fuller;
      } catch { /* Keep the presentation document and show it on the error. */ }
      return primary;
    };
    const id = String(payload.draft_url || '').match(/\/sellinghub\/drafts\/edit\/([A-Za-z0-9_-]{1,80})\/?$/)?.[1];
    const path = `/presentation/api/v1/drafts/${id}/`;
    const description = depopDescription(payload.listing);
    const summary = draft => {
      const view = depopDraftView(draft);
      const pictureIds = depopPictureIds(draft, null);
      return { id: String(draft.id), description: draft.description, price: view.price,
        product_type: view.product_type, variant_set_id: view.variant_set, variants: view.variants,
        brand: view.brand, condition: view.condition, colour: view.colour, age: view.age, source: view.source, style: view.style,
        attributes: view.attributes, sku: view.sku, quantity: view.quantity,
        international_shipping_cost: view.international_shipping_cost,
        picture_ids: pictureIds, photo_count: pictureIds.length || depopPictureRows(draft).length,
        missing_fields: draft.missing_fields || draft.missingFields || [] };
    };
    if (action === 'session') {
      await request('GET', '/presentation/api/v1/drafts/?limit=24&sort_by=createdAt');
      return { authenticated: true };
    }
    if (action === 'find' || action === 'reuse') {
      const drafts = await request('GET', '/presentation/api/v1/drafts/?limit=24&sort_by=createdAt');
      const rows = Array.isArray(drafts?.objects) ? drafts.objects : [];
      const found = rows.filter(draft => depopDraftMatches(draft, payload.listing) && /^[A-Za-z0-9_-]{1,80}$/.test(String(draft.id)));
      const draftLink = draft => ({ ...summary(draft), draft_url: `https://www.depop.com/sellinghub/drafts/edit/${draft.id}/` });
      if (action === 'reuse') {
        if (found.length <= 1) return found[0] ? draftLink(found[0]) : { draft_url: '' };
        // Several drafts share this description and price. The stored draft wins.
        // Otherwise the same id is chosen on every retry, never whichever was edited last.
        const stored = found.find(draft => String(draft.id) === id);
        const chosen = stored || [...found].sort((a, b) => String(a.id).localeCompare(String(b.id)))[0];
        if (!stored) marketplaceLog('depop', 'reuse', { matches: found.length, draft: chosen.id });
        return draftLink(chosen);
      }
      if (found.length !== 1) throw new Error('Depop could not identify this draft uniquely. Review drafts in Chrome.');
      return draftLink(found[0]);
    }
    if (action === 'create') {
      const draft = await request('POST', '/presentation/api/v1/drafts/', payload.depop_fields
        ? depopDraftWriteBody(payload.listing, payload.depop_fields, [])
        : { description, price_amount: String(depopPrice(payload.listing) ?? ''), price_currency: 'USD', pictures: [], quantity: 1 });
      if (!/^[A-Za-z0-9_-]{1,80}$/.test(String(draft.id))) throw marketplaceError('Depop did not confirm its new draft ID. Check drafts before trying again.', { code: 'schema_drift', rejected: false });
      return { draft_url: `https://www.depop.com/sellinghub/drafts/edit/${draft.id}/` };
    }
    if (!id && action !== 'publish_status' && action !== 'update') throw marketplaceError('A saved Depop draft link is required.');
    if (action === 'read') {
      const draft = await readDraft();
      const detail = [depopReadDigest(draft), depopSanitizedBody(draft)].filter(Boolean).join(' ');
      marketplaceLog('depop', 'read-back', { body: detail || null });
      return { ...summary(draft), read_back: detail };
    }
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
      let fields = payload.depop_fields;
      // A direct save has no precomputed fields. Fill category, brand, location, and shipping here.
      if (!fields) fields = await depopListingFields(request, payload.listing, payload.deadline);
      else fields = await depopWithCountry(request, fields, payload.deadline);
      let pictures = existing;
      if (payload.uploaded_photo_ids?.length) {
        pictures = await depopProcessedPhotos(request, payload.uploaded_photo_ids);
        if (pictures.length !== depopPictureIds(payload.uploaded_photo_ids, null).length) {
          throw marketplaceError(`Depop has not finished processing ${depopPictureIds(payload.uploaded_photo_ids, null).length - pictures.length} of the photos. Try again in a minute.`,
            { code: 'rejected', marketplace_message: `valid picture ids: ${pictures.join(', ') || '(none)'}` });
        }
      }
      const draft = depopDraftWriteBody(payload.listing, fields, pictures, existing);
      if (draft.national_shipping_cost == null && existing?.national_shipping_cost
        && typeof existing.national_shipping_cost !== 'object') {
        draft.national_shipping_cost = String(existing.national_shipping_cost);
      }
      const reply = await request('PUT', path, draft);
      let saved = await readDraft();
      for (let attempt = 0; attempt < 2 && !depopPhotosKept(saved, draft.pictures); attempt += 1) {
        await marketplaceSleep(300);
        saved = await readDraft();
      }
      const wrong = depopDraftMismatch(saved, payload.listing, payload.depop_fields);
      const photosKept = depopPhotosKept(saved, draft.pictures);
      if (wrong.length || !photosKept) {
        const parts = [...wrong, ...(!photosKept ? ['photos'] : [])];
        throw keptError(parts, saved, reply);
      }
      return summary(saved);
    }
    if (action === 'prepare_publish' || action === 'publish') {
      if (payload.action !== 'publish' || payload.confirm !== true) throw marketplaceError('Confirm listing on Depop.');
      const draft = await request('GET', path);
      const missing = [...(Array.isArray(draft.missing_fields) ? draft.missing_fields : []).map(String),
        ...(!draft.pictures?.length ? ['photos'] : []), ...(!(Number(draft.price_amount) > 0) ? ['price'] : [])];
      if (missing.length || !Array.isArray(draft.missing_fields)) {
        const named = Array.isArray(draft.missing_fields) ? draft.missing_fields.map(String).join(', ') : '';
        throw marketplaceMissing('depop', [...new Set(missing.length ? missing : ['details'])]
          .map(field => ({ field, needs: depopFieldNeeds(field) })), named ? `missing_fields: ${named}` : '');
      }
      const wrong = depopDraftMismatch(draft, payload.listing, payload.depop_fields);
      if (wrong.length) throw marketplaceError(`Depop's draft has a different ${wrong.join(', ')} than this revision. List again.`);
      if (action === 'prepare_publish') return { ok: true };
      const job = await request('POST', '/presentation/api/v1/drafts/products/', { draft_ids: [id] });
      if (!/^[A-Za-z0-9_-]+$/.test(String(job.id))) throw marketplaceError('Depop did not confirm the publish result. Check its result before trying again.', { code: 'schema_drift', rejected: false });
      return { publish_job_id: String(job.id) };
    }
    if (action === 'update') {
      if (payload.confirm !== true) throw marketplaceError('Confirm updating the live Depop listing.');
      const slug = String(payload.listing_id || '').trim().toLowerCase();
      if (!/^[a-z0-9][a-z0-9-]{0,200}$/.test(slug)) throw marketplaceError('A Depop listing link is required before updating.');
      const product = await request('GET', `/presentation/api/v1/products/by-slug/${slug}/`);
      const productId = String(product?.id || '');
      if (!/^[0-9]+$/.test(productId)) throw marketplaceError('Depop did not confirm this listing. Check it on Depop before trying again.', { code: 'schema_drift', rejected: false });
      const fields = await depopLiveFields(request, payload, product);
      const body = depopLiveWriteBody(product, payload.listing, fields);
      if (!body.picture_ids.length) {
        throw marketplaceError('Depop did not return this listing\'s photo ids, so nothing was changed. Check the listing on Depop.',
          { code: 'schema_drift', marketplace_message: depopReadDigest(product) });
      }
      const updated = await request('PUT', `/presentation/api/v1/products/by-slug/${slug}/`, body);
      // The PUT answer can be the product as it was before the edit, without a
      // price, so it never decides the outcome. Fresh reads do, with a short
      // wait for Depop's read-after-write lag.
      const returned = String(updated?.slug || updated?.product_slug || '').trim().toLowerCase();
      const next = /^[a-z0-9][a-z0-9-]{0,200}$/.test(returned) ? returned : slug;
      const wanted = depopLiveWanted(payload.listing, fields);
      let state = {};
      let wrong = [];
      let read = null;
      for (const wait of DEPOP_LIVE_READ_WAITS_MS) {
        if (wait) {
          if (payload.deadline && Date.now() + wait > payload.deadline - 2000) break;
          await marketplaceSleep(wait);
        }
        const docs = [];
        for (const candidate of [...new Set([next, slug])]) {
          for (const suffix of ['edit-listing/', '']) {
            try { docs.push(await request('GET', `/presentation/api/v1/products/by-slug/${candidate}/${suffix}`)); } catch { /* Try the other read. */ }
          }
          if (docs.length) break;
        }
        if (!docs.length) continue;
        read = docs[0];
        state = depopLiveState(docs);
        wrong = depopLiveMismatch(state, wanted);
        if (!wrong.length) break;
      }
      if (!read) {
        throw marketplaceError('Depop saved the edit but the listing could not be read back. Check the live listing.', { code: 'schema_drift', rejected: false });
      }
      if (wrong.length) {
        throw marketplaceError(`Depop did not keep this listing's ${wrong.join(', ')}. Check the live listing.`, { code: 'schema_drift', rejected: false,
          marketplace_message: `read back: ${depopLiveDigest(state)} | wanted: ${depopLiveDigest(wanted)} | ${depopSanitizedBody(read)}` });
      }
      const unconfirmed = ['price', 'product_type'].filter(key => wanted[key] != null && wanted[key] !== '' && (state[key] == null || state[key] === ''));
      return { ok: true, state: 'published', listing_url: `https://www.depop.com/products/${next}/`, previous_slug: slug, price: state.price ?? null,
        ...(unconfirmed.length ? { warning: `Depop's read did not show the ${unconfirmed.map(key => key === 'product_type' ? 'category' : key).join(' or ')}. Check the live listing.` } : {}) };
    }
    if (action === 'publish_status') {
      if (!/^[A-Za-z0-9_-]+$/.test(String(payload.publish_job_id))) throw marketplaceError('A Depop publish receipt is required.');
      const status = await request('GET', `/api/v3/drafts/products/${payload.publish_job_id}/status/`);
      const complete = status.count_by_status?.COMPLETE === 1 && status.count_by_status?.PENDING === 0;
      const pending = status.count_by_status?.PENDING > 0;
      // The status counts say the draft posted. The public link is a separate field, or the seller's selling list.
      // Publish status is only a count. The public link is on the product, or on the one shop row that is this listing.
      let listing_url = complete ? depopStatusUrl(status, id) : '';
      if (complete && !listing_url && id) {
        try {
          const draft = await request('GET', `/presentation/api/v1/drafts/${id}/`);
          listing_url = depopListingUrl(draft?.slug || draft?.product_slug || draft?.product?.slug || draft?.url);
        } catch { /* A posted draft is gone. The shop row is the remaining place to read the link. */ }
      }
      if (complete && !listing_url) listing_url = await depopShopUrl(request, payload);
      return { complete, pending, ...(listing_url ? { listing_url } : {}) };
    }
    throw marketplaceError('Unsupported Depop action.');
  } catch (error) {
    return marketplaceFailure(error, !wrote);
  }
}

function depopDraftGone(error) {
  return /could not find draft/i.test(`${error?.message || ''} ${error?.marketplace_message || ''}`);
}

function depopListingUrl(value) {
  const text = String(value || '').trim();
  const absolute = /^https:\/\/www\.depop\.com\/products\/([a-z0-9][a-z0-9-]{0,200})\/?$/i.exec(text);
  if (absolute) return `https://www.depop.com/products/${absolute[1].toLowerCase()}/`;
  const slug = text.toLowerCase();
  return /^[a-z0-9][a-z0-9-]{0,200}$/.test(slug) ? `https://www.depop.com/products/${slug}/` : '';
}

// One public product from a publish-status body. Several unnamed products stay unresolved.
function depopStatusUrl(status, draftId) {
  const rows = [];
  const lists = [status?.products, status?.drafts, status?.items, status?.objects];
  if (status?.product) lists.push([status.product]);
  for (const list of lists) {
    if (!Array.isArray(list)) continue;
    for (const row of list) {
      const url = depopListingUrl(row?.slug || row?.url || row?.listing_url);
      if (!url) continue;
      const draft = String(row?.draft_id || row?.draftId || '');
      rows.push({ url, prefer: Boolean(draftId) && draft === String(draftId) });
    }
  }
  const preferred = rows.filter(row => row.prefer);
  const chosen = preferred.length === 1 ? preferred : rows.length === 1 ? rows : [];
  return chosen[0]?.url || '';
}

function depopProductPrice(product) {
  const pricing = product?.pricing && typeof product.pricing === 'object' ? product.pricing : {};
  const raw = product?.price_amount ?? product?.price?.price_amount ?? pricing.price_amount
    ?? pricing.current_price ?? pricing.original_price?.price_amount ?? pricing.original_price
    ?? (typeof product?.price === 'string' || typeof product?.price === 'number' ? product.price : null);
  // No price on the document means the price is unknown, not zero.
  const value = raw && typeof raw === 'object' ? raw.price_amount ?? raw.price : raw;
  if (value == null || String(value).trim() === '') return null;
  const price = Number(value);
  return Number.isFinite(price) ? price : null;
}

function depopShopProduct(product, listing) {
  const text = String(product?.description || '');
  const base = depopBaseDescription(listing);
  const title = String(listing?.title || '').trim();
  const textMatch = (Boolean(base) && text.startsWith(base)) || (title.length > 8 && text.includes(title));
  if (!textMatch || !depopListingUrl(product?.slug || product?.url)) return false;
  if (product?.user_id != null && listing?.seller_id != null && String(product.user_id) !== String(listing.seller_id)) return false;
  const price = depopProductPrice(product);
  const wanted = Number(listing?.price);
  return price == null || !Number.isFinite(wanted) || price === wanted;
}

// Reads that can name the product a finished publish created. The selling list
// without a sort starts at the oldest rows, so the new one is not on that page.
function depopShopRead(path) {
  const selling = /^\/presentation\/api\/v1\/shops\/\d{1,15}\/products\/by-status\/selling\/\?limit=24$/;
  const shop = /^\/api\/v1\/shop\/\d{1,15}\/products\/\?limit=200(?:&offset_id=[A-Za-z0-9._~%-]{1,220})?$/;
  return selling.test(path) || shop.test(path);
}

function depopProductRows(page) {
  for (const key of ['objects', 'products', 'data']) {
    if (Array.isArray(page?.[key])) return page[key];
  }
  return [];
}

function depopCursorToken(value) {
  const text = encodeURIComponent(String(value ?? ''));
  return /^[A-Za-z0-9._~%-]{1,220}$/.test(text) ? text : '';
}

function depopShopMatch(page, listing) {
  const matches = [...new Set(depopProductRows(page)
    .filter(product => depopShopProduct(product, listing))
    .map(product => depopListingUrl(product?.slug || product?.url)))];
  return matches.length === 1 ? matches[0] : '';
}

// Publish status names no product. The selling list is a short window that can
// miss a listing just posted, so the shop inventory is what carries the slug.
async function depopShopUrl(request, payload) {
  const listing = payload.listing || {};
  if (!depopBaseDescription(listing) || !Number.isFinite(Number(listing.price))) return '';
  let me;
  try { me = await request('GET', '/presentation/api/v1/users/me/'); } catch { return ''; }
  const userId = String(me?.id ?? '');
  if (!/^\d{1,15}$/.test(userId)) return '';
  const scoped = { ...listing, seller_id: userId };
  const read = async path => {
    if (!depopShopRead(path)) return null;
    try { return await request('GET', path); } catch { return null; }
  };
  const early = depopShopMatch(await read(`/presentation/api/v1/shops/${userId}/products/by-status/selling/?limit=24`), scoped);
  if (early) return early;
  const inventory = `/api/v1/shop/${userId}/products/?limit=200`;
  let offset = '';
  let previousId;
  for (let pageNumber = 0; pageNumber < 8; pageNumber += 1) {
    const page = await read(offset ? `${inventory}&offset_id=${offset}` : inventory);
    const url = depopShopMatch(page, scoped);
    if (url) return url;
    const firstId = depopProductRows(page)[0]?.id;
    if (!page || (pageNumber && firstId != null && firstId === previousId) || page?.meta?.end === true) break;
    previousId = firstId;
    const next = depopCursorToken(page?.meta?.last_offset_id);
    if (!next || next === offset) break;
    offset = next;
  }
  return '';
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
  const storeReceipt = async receipt => {
    const journal = (await chrome.storage.local.get(key))[key] || {};
    await chrome.storage.local.set({ [key]: { ...journal, ...receipt } });
  };
  const verify = async () => {
    const draft = await depopAction('read', payload);
    const wrong = depopDraftMismatch(draft, payload.listing, payload.depop_fields);
    const photos = payload.expected_photo_count != null && draft.photo_count !== payload.expected_photo_count;
    if (wrong.length || photos) {
      const parts = [...wrong, ...(photos ? ['photos'] : [])];
      const error = new Error(`Depop did not keep this listing's ${parts.join(', ')}. List again.`);
      if (draft.read_back) error.marketplace_message = draft.read_back;
      throw error;
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
    const chosen = depopChosenPhotos(payload.photos);
    payload.photos = chosen.photos;
    if (!payload.photos?.length) {
      throw marketplaceMissing('depop', [{ field: 'photos', needs: 'between one and eight photos' }]);
    }
    if (chosen.omitted) payload.photo_warning = `Depop kept 8 photos and left ${chosen.omitted} off.`;
  };
  try {
    if (payload.action === 'update') {
      const updated = await depopAction('update', payload);
      const warning = [updated.warning, payload.photo_warning].filter(Boolean).join(' ');
      return { ...updated, ...(warning ? { warning } : {}) };
    }
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
      if (!payload.draft_url) {
        const reused = await depopAction('reuse', payload);
        if (reused.draft_url) {
          payload.draft_url = nativeDraftUrl('depop', reused.draft_url);
          if (payload.draft_url) await storeReceipt({ receipt_url: payload.draft_url });
        }
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
      // A reused draft with no picture ids still needs this listing's photos.
      // URL-only rows are not ids Depop will keep on the next write.
      if (!(existing.picture_ids || []).length) {
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
      // What this revision resolves to on Depop, so publish refuses a draft
      // whose category, size or attributes are no longer the ones sent.
      payload.depop_fields = await depopResolvedFields(payload);
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
      if (result.complete) return { ok: true, state: 'published', draft_url: payload.draft_url, ...(result.listing_url ? { listing_url: result.listing_url } : {}) };
      if (!result.pending) throw new Error('Depop did not confirm the listing. Check Depop before listing again.');
      await marketplaceSleep(2000);
    }
    throw new Error('Depop has not confirmed publishing yet. Check its result later.');
  } catch (error) {
    if (depopDraftGone(error) && !replacedDraft) payload.draft_url = '';
    // A write may have landed and the reply was lost. Read Depop before answering
    // so a finished action is a draft, a listing, or a refusal — never a guess.
    if (committed && payload.draft_url) {
      try {
        if (payload.publish_job_id) {
          const status = await depopAction('publish_status', payload);
          if (status.complete) return { ok: true, state: 'published', draft_url: payload.draft_url, ...(status.listing_url ? { listing_url: status.listing_url } : {}) };
        } else if (payload.action === 'save_draft') {
          return await verify();
        }
      } catch (readError) {
        if (depopDraftGone(readError)) {
          return marketplaceFailure(error, true, {});
        }
        if (payload.action === 'save_draft') {
          return marketplaceFailure(readError, true, {
            draft_url: payload.draft_url, ...(replacedDraft ? { draft_replaced: true } : {}),
          });
        }
      }
    }
    return marketplaceFailure(error, !committed, {
      ...(payload.draft_url ? { draft_url: payload.draft_url } : {}),
      ...(replacedDraft && payload.draft_url ? { draft_replaced: true } : {}),
    });
  }
}
