// Poshmark one-step listing from the extension worker. Only Connect opens a tab.
// The steps are the listing editor's own (read 2026-10-24): create a post,
// upload photos, save every field, publish. The post is never saved as a
// visible draft. Seller-confirmed only, sent once, never retried.
const POSHMARK = 'https://poshmark.com';
const POSHMARK_QUERY = 'app_version=2.55&pm_version=2026.43.00';
const POSHMARK_CONDITIONS = new Set(['nwt', 'uln', 'ug', 'uf']);
const POSHMARK_STYLE_TAG = /^[A-Za-z0-9\-, ']{1,25}$/;

// Poshmark's public catalog: departments, categories, size sets and colours.
async function poshmarkCatalogData(deadline) {
  return await marketplaceCatalog('poshmark:catalog', () => marketplaceCatalogRead('poshmark', 'category list', async () => {
    const { data } = await marketplaceFetch(`${POSHMARK}/vm-rest/meta/catalog_v2`, { marketplace: 'poshmark', step: 'catalog',
      credentials: 'omit', headers: { Accept: 'application/json' }, deadline, timeoutMs: 30000 });
    const catalog = data.data || data;
    if (!Array.isArray(catalog?.catalog?.departments)) {
      throw marketplaceError('Poshmark\'s category list changed.', { code: 'schema_drift' });
    }
    return catalog;
  }));
}

// Poshmark's own style tags. Free text is accepted too, so a failed read
// only means the listing's tags go as the seller wrote them.
async function poshmarkStyleTags(deadline) {
  try {
    return await marketplaceCatalog('poshmark:style_tags', async () => {
      const { data } = await marketplaceFetch(`${POSHMARK}/vm-rest/meta/style_tags/top?${POSHMARK_QUERY}`, { marketplace: 'poshmark',
        step: 'style-tags', credentials: 'omit', headers: { Accept: 'application/json' }, deadline, timeoutMs: 10000 });
      return Array.isArray(data.data) ? data.data.map(String) : [];
    });
  } catch (error) {
    marketplaceLog('poshmark', 'style-tags', { error_code: error.code || 'failed' });
    return [];
  }
}

// Poshmark's own spelling of the brand (brand search, read 2026-10-25). Brand
// is a free string on a post, so a brand Poshmark does not know, or a failed
// search, keeps the seller's spelling.
async function poshmarkBrand(listing, deadline) {
  const wanted = String(listing.form?.overrides?.brand || listing.poshmark_specifics?.poshmarkBrand || listing.brand || '').trim();
  if (!wanted || /^(?:unbranded|no brand|none|other|n\/a)$/i.test(wanted)) return '';
  try {
    const brands = await marketplaceCatalog(`poshmark:brand:${marketplaceNameKey(wanted)}`, async () => {
      const { data } = await marketplaceFetch(`${POSHMARK}/vm-rest/searches/brands/suggested?query=${encodeURIComponent(wanted)}&count=10&${POSHMARK_QUERY}`,
        { marketplace: 'poshmark', step: 'brand', credentials: 'omit', headers: { Accept: 'application/json' }, deadline, timeoutMs: 10000 });
      return Array.isArray(data.data) ? data.data : [];
    });
    const match = brands.find(entry => marketplaceNameKey(entry?.brand_name) === marketplaceNameKey(wanted));
    return match ? String(match.brand_name) : wanted;
  } catch (error) {
    marketplaceLog('poshmark', 'brand', { error_code: error.code || 'failed' });
    return wanted;
  }
}

// Up to three style tags: the seller's Poshmark tags, else the listing's tags
// Poshmark already has, in its spelling, else the first three tags, which
// Poshmark takes as free text (Vendoo sends them the same way).
function poshmarkPostStyleTags(listing, known) {
  const valid = values => values.map(tag => String(tag).trim()).filter(tag => POSHMARK_STYLE_TAG.test(tag));
  const byKey = new Map(known.map(tag => [marketplaceNameKey(tag), tag]));
  const spelled = tag => byKey.get(marketplaceNameKey(tag)) || tag;
  const own = valid(Array.isArray(listing.poshmark_specifics?.styleTags) ? listing.poshmark_specifics.styleTags : []);
  if (own.length) return [...new Set(own.map(spelled))].slice(0, 3);
  const general = valid(listingTags(listing));
  const matched = [...new Set(general.map(tag => byKey.get(marketplaceNameKey(tag))).filter(Boolean))];
  return (matched.length ? matched : [...new Set(general)]).slice(0, 3);
}

// The signed-in account from Poshmark's jwt cookie, checked before any request.
async function poshmarkAccount() {
  const claims = jwtClaims(cookieToken(await chrome.cookies.get({ url: `${POSHMARK}/`, name: 'jwt' })));
  if (!/^[a-f0-9]{24}$/.test(String(claims?.user_id))) {
    throw marketplaceError('Reconnect Poshmark in Anteroom first. No tab will be opened automatically.', { code: 'signed_out' });
  }
  if (Number.isFinite(claims.exp) && claims.exp * 1000 <= Date.now()) {
    throw marketplaceError('Your Poshmark sign-in has expired. Reconnect Poshmark in Anteroom.', { code: 'signed_out' });
  }
  return { user_id: String(claims.user_id) };
}

// Poshmark's listing page carries the session's request token in its initial
// state; its own editor sends it as X-XSRF-TOKEN on every write.
async function poshmarkCsrf(deadline) {
  const { data: page } = await marketplaceFetch(`${POSHMARK}/create-listing`, { marketplace: 'poshmark', step: 'session',
    headers: { Accept: 'text/html' }, expect: 'text', deadline, timeoutMs: 15000 });
  let state = null;
  try { state = JSON.parse(/__INITIAL_STATE__\s*=\s*({[^<]*});/.exec(page)?.[1] || 'null'); } catch {}
  if (!state || typeof state !== 'object') {
    throw marketplaceError('Poshmark\'s listing page changed. Anteroom needs an update before listing there.', { code: 'schema_drift' });
  }
  const user = state.userCookies?.userInfo || {};
  if (!(user.uid || user.id)) throw marketplaceError('Poshmark asked to sign in again. Reconnect Poshmark in Anteroom.', { code: 'signed_out' });
  if (typeof state.csrftoken !== 'string' || !state.csrftoken) marketplaceLog('poshmark', 'session', { csrf: 'missing' });
  return typeof state.csrftoken === 'string' ? state.csrftoken : '';
}

// Poshmark answers most refusals with HTTP 200 and an error body.
function poshmarkRefusal(error, what) {
  const type = String(error?.errorType || '');
  const own = marketplaceBodyMessage({ error });
  const code = /auth|session|login|signin/i.test(type) ? 'signed_out' : /rate|limit|toomany/i.test(type) ? 'rate_limited' : 'rejected';
  // A validation refusal names what the listing lacks; Anteroom can ask for it.
  const refusal = /validation|missing|invalid(?:input|param|field)/i.test(type)
    ? marketplaceError('Poshmark needs more details before it can list this.', { code: 'missing_fields', marketplace_message: own,
      fields: [{ field: String(error.field || error.param || type), message: marketplaceScrub(own) || 'Poshmark needs more details' }] })
    : marketplaceError(code === 'signed_out' ? 'Poshmark asked to sign in again. Reconnect Poshmark in Anteroom.'
      : `Poshmark refused ${what}.`, { code, marketplace_message: own });
  return Object.assign(refusal, { marketplace_type: type });
}

function poshmarkClient(deadline, csrf) {
  const client = { csrf };
  client.call = async (method, path, body, { step = 'request', what = 'the request', retry = method === 'GET', guard = true, timeoutMs = 25000 } = {}) => {
    const multipart = body instanceof FormData;
    const { data } = await marketplaceFetch(`${POSHMARK}${path}${path.includes('?') ? '&' : '?'}${POSHMARK_QUERY}`, {
      marketplace: 'poshmark', step, method, retry, guard, timeoutMs, deadline: guard ? deadline : Date.now() + timeoutMs,
      headers: { Accept: 'application/json', ...(method !== 'GET' && client.csrf ? { 'X-XSRF-TOKEN': client.csrf } : {}),
        ...(!multipart && method !== 'GET' ? { 'Content-Type': 'application/json' } : {}) },
      ...(body === undefined ? {} : { body: multipart ? body : JSON.stringify(body) }) });
    if (data.error) throw poshmarkRefusal(data.error, what);
    return data.data || data;
  };
  // Photo uploads are safe to repeat. A 400 usually means a stale request
  // token, so the token is read again once, as Poshmark's own editor does.
  client.photo = async (postId, image) => {
    const bytes = Uint8Array.from(atob(image.base64), char => char.charCodeAt(0));
    for (let attempt = 0; ; attempt += 1) {
      const form = new FormData();
      form.append('file', new Blob([bytes], { type: image.mime_type }), image.name);
      try {
        const uploaded = await client.call('POST', `/api/posts/${postId}/media/scratch?app_type=web`, form, { step: 'photo', what: 'a photo', retry: true });
        if (!/^[a-f0-9]{24}$/.test(String(uploaded.id))) {
          throw marketplaceError('Poshmark did not confirm a photo upload.', { code: 'schema_drift' });
        }
        return String(uploaded.id);
      } catch (error) {
        if (error.status !== 400 || attempt) throw error;
        client.csrf = await poshmarkCsrf(deadline);
      }
    }
  };
  return client;
}

// Anteroom/Vendoo condition labels to Poshmark's own codes. Studio's form already carries the code.
function poshmarkCondition(listing) {
  const native = String(listing.form?.overrides?.condition || listing.poshmark_specifics?.condition || '').toLowerCase();
  if (POSHMARK_CONDITIONS.has(native)) return native;
  const text = String(listing.poshmark_specifics?.poshmarkCondition || listing.condition || '').toLowerCase();
  if (/new with tags|\bnwt\b/.test(text) && !/without/.test(text)) return 'nwt';
  if (/new without tags|\bnwot\b|like new|excellent/.test(text)) return 'uln';
  if (/fair|poor/.test(text)) return 'uf';
  if (/good|imperfection|defect|pre-owned|used/.test(text)) return 'ug';
  if (/^new\b|brand new/.test(text)) return 'nwt';
  return null;
}

// Poshmark takes whole dollars only. Vendoo rounds to the nearest one.
function poshmarkPrice(listing) {
  return Math.round(Number(listing?.poshmark_specifics?.poshmarkPrice || listing?.price));
}

// The post's catalog from the leaf's id chain. Studio resolves the chain from
// the category tree; Poshmark's "Global & Traditional Wear" is a grouping in
// that tree, not a catalog level, so its id is skipped.
function poshmarkCategory(catalog, listing) {
  const chain = listing.form?.overrides?.categoryV2?.path || listing.marketplace_category_objects?.poshmark?.path;
  const ids = (Array.isArray(chain) ? chain : []).map(String).filter(id => !id.startsWith('group:'));
  if (ids.length < 2 || ids.length > 3) return null;
  const department = catalog.catalog.departments.find(entry => entry.id === ids[0]);
  const category = department?.categories.find(entry => entry.id === ids[1]);
  if (!category) return null;
  const feature = ids[2] && ids[2] !== ids[1] ? category.category_features?.find(entry => entry.id === ids[2]) : null;
  if (ids[2] && ids[2] !== ids[1] && !feature) return null;
  return { department, category, leaf: feature ? feature.id : category.id,
    catalog: { department: department.id, category: category.id, category_features: feature ? [feature.id] : [] } };
}

const POSHMARK_FIT_LABELS = { petite: 'Petite', plus: 'Plus', 'big_&_tall': 'Big & Tall', maternity: 'Maternity', juniors: 'Juniors' };

// A size inside the category's own size sets, as { set, size } or { problem }.
// Vendoo's coded size (``<leaf>_size`` with its ``_scale`` set tag) wins.
// Otherwise the seller's size is matched only inside the sets their listing
// allows: a kids' size inside the boys' or girls' set the listing names, a
// fit (Petite, Plus, Big & Tall...) inside that set. Nothing crosses them.
function poshmarkSize(category, listing, { kids = false, leaf = category.id } = {}) {
  const specifics = listing.poshmark_specifics || {};
  const aspects = listing.form?.aspects || {};
  const sets = category.size_sets || [];
  const sizesOf = set => set.size_systems?.us?.sizes || [];
  const all = sets.flatMap(set => sizesOf(set).map(size => ({ set, size })));
  if (all.length && all.every(entry => entry.size.id === 'OS')) return { ...all[0], quantity: true };
  const coded = String(aspects[`${leaf}_size`] ?? '').trim();
  const codedScale = String(aspects[`${leaf}_size_scale`] ?? '').trim();
  if (coded && codedScale === 'customSize') return { set: { tags: [] }, size: { id: coded, display: coded } };
  if (coded) {
    const found = all.find(entry => entry.size.id === coded && (!codedScale || entry.set.tags?.includes(codedScale)));
    if (found) return found;
  }
  const wanted = String(specifics[`${category.id}Size`] || specifics.size || listing.size || '').trim();
  if (!wanted) return null;
  const tagged = tag => sets.filter(set => set.tags?.includes(tag));
  const scale = String(specifics[`${category.id}SizeScale`] || '').trim() || codedScale;
  let groups;
  if (scale) {
    groups = [tagged(scale)];
  } else if (kids && sets.some(set => set.tags?.includes('boys') || set.tags?.includes('girls'))) {
    const gender = listingKidsGender(listing);
    if (!gender) return { problem: 'whether this is a boys\' or girls\' size' };
    groups = [tagged(gender), tagged('baby')];
  } else {
    const fit = listingSizeFit(listing);
    if (fit) {
      if (!tagged(fit).length) return { problem: `a size from a set Poshmark offers here (it has no ${POSHMARK_FIT_LABELS[fit] || fit} sizes in this category)` };
      groups = [tagged(fit)];
    } else {
      const standard = tagged('standard');
      // Past the standard range, Poshmark keeps larger sizes in Plus or Big & Tall.
      groups = standard.length ? [standard, [...tagged('plus'), ...tagged('big_&_tall')]] : [sets];
    }
  }
  for (const group of groups) {
    const entries = group.flatMap(set => sizesOf(set).map(size => ({ set, size,
      keys: sizeEntryKeys([size.id, size.short, size.display], { petite: set.tags?.includes('petite') }) })));
    const found = matchSize(wanted, entries);
    if (found.length) return { set: found[0].set, size: found[0].size };
  }
  return null;
}

function poshmarkPostFields(catalog, listing, { brand = '', styleTags = [] } = {}) {
  const missing = [];
  const category = poshmarkCategory(catalog, listing);
  if (!category) missing.push({ field: 'category', needs: 'a Poshmark category' });
  const size = category ? poshmarkSize(category.category, listing, { kids: category.department.display === 'Kids', leaf: category.leaf }) : null;
  if (category && !size?.size) missing.push({ field: 'size', needs: size?.problem || 'a size Poshmark offers in this category' });
  const condition = poshmarkCondition(listing);
  if (!condition) missing.push({ field: 'condition', needs: 'a condition' });
  const price = poshmarkPrice(listing);
  if (!(price >= 3)) missing.push({ field: 'price', needs: 'a price of $3 or more' });
  const title = String(listing.title || '').trim();
  if (!title) missing.push({ field: 'title', needs: 'a title' });
  if (title.length > 80) missing.push({ field: 'title', needs: 'a title of 80 characters or fewer' });
  const description = String(listing.description || '').trim();
  if (!description) missing.push({ field: 'description', needs: 'a description' });
  if (description.length > 1500) missing.push({ field: 'description', needs: 'a description of 1500 characters or fewer' });
  if (missing.length) throw marketplaceMissing('poshmark', missing);
  const colorNames = new Set((catalog.colors || []).map(color => color.name));
  const colors = [listing.poshmark_specifics?.poshmarkColor || listing.primaryColor, listing.secondaryColor]
    .map(value => ({ Grey: 'Gray', Beige: 'Tan' })[value] || value).filter(value => colorNames.has(value));
  const smart = listing.form?.specifics?.smartSell;
  const minimum = Math.round(Number(smart?.enabled && smart.minPrice) || Number(listing.poshmark_specifics?.smartSellMinimumPrice || listing.poshmark_specifics?.smartSell?.minimumPrice || 0));
  const sku = String(listing.sku || listing.poshmark_specifics?.poshmarkSku || '').trim().slice(0, 50);
  const cost = Math.round(Number(listing.poshmark_specifics?.costPrice || listing.costPrice || 0));
  return {
    title, description,
    catalog: category.catalog,
    inventory: { status: 'available', multi_item: false, size_quantity_revision: 0, size_quantities: [{
      size_id: size.size.id, size_obj: size.size, size_system: 'us', quantity_available: 1, quantity_sold: 0,
      size_set_tags: size.set.tags || [], seller_inventory_private_info: {} }] },
    price_amount: { val: String(price), currency_code: 'USD', currency_symbol: '$' },
    original_price_amount: { val: '0', currency_code: 'USD', currency_symbol: '$' },
    condition,
    ...(brand ? { brand } : {}),
    colors: [...new Set(colors)].slice(0, 2),
    style_tags: styleTags,
    offer_auto_actions_v2_enabled: minimum > 0 && minimum < price,
    offer_auto_actions_min_price_amount: minimum > 0 && minimum < price ? { val: String(minimum), currency_code: 'USD' } : null,
    ...(sku || cost > 0 ? { seller_private_info: { ...(sku ? { sku } : {}),
      ...(cost > 0 ? { cost_price_amount: { val: String(cost), currency_code: 'USD' } } : {}) } } : {}),
  };
}

// Every catalog read the post needs, before anything is written.
async function poshmarkPreparedPost(listing, deadline) {
  const catalog = await poshmarkCatalogData(deadline);
  const brand = await poshmarkBrand(listing, deadline);
  const styleTags = poshmarkPostStyleTags(listing, await poshmarkStyleTags(deadline));
  return poshmarkPostFields(catalog, listing, { brand, styleTags });
}

function poshmarkListingUrl(id) {
  return `https://poshmark.com/listing/${id}`;
}

function poshmarkPhotoCount(photos) {
  if (!photos?.length || photos.length > 16) {
    throw marketplaceMissing('poshmark', [{ field: 'photos', needs: 'between one and sixteen photos' }]);
  }
}

// Edit the post that is already published. The same field save the editor uses,
// without creating a post and without calling publish again.
async function updatePoshmarkPost(msg, recovery = false) {
  const p = msg.payload;
  const deadline = Date.now() + 150000;
  const id = String(p.listing_id || '');
  const key = `native_operation_${p.operation_id}`;
  let stage = 'prepare';
  const matches = post => post.status === 'published'
    && marketplaceText(post.title) === marketplaceText(String(p.listing?.title || '').trim())
    && String(post.price_amount?.val) === String(poshmarkPrice(p.listing));
  try {
    if (!/^[a-f0-9]{24}$/.test(id) || (p.confirm !== true && !recovery)) {
      return marketplaceFailure(marketplaceError('Confirm updating the live Poshmark listing.'), true);
    }
    const account = await poshmarkAccount();
    const client = poshmarkClient(deadline, '');
    const existing = await client.call('GET', `/vm-rest/posts/${id}`, undefined, { step: 'read' });
    if (String(existing.creator_id) !== account.user_id) throw marketplaceError('This Poshmark listing belongs to another account.');
    const sold = (existing.inventory?.size_quantities || []).some(entry => Number(entry.quantity_sold) > 0);
    if (sold || existing.status !== 'published') throw marketplaceError('This Poshmark listing is not live. It was not changed.');
    if (recovery) {
      if (!matches(existing)) return marketplaceFailure(new Error('Poshmark has not confirmed the update. Check the live listing before trying again.'), false);
      return { ok: true, state: 'published', listing_url: poshmarkListingUrl(id) };
    }
    poshmarkPhotoCount(p.photos);
    const post = await poshmarkPreparedPost(p.listing || {}, deadline);
    post.inventory.size_quantity_revision = Number(existing.inventory?.size_quantity_revision) || 0;
    client.csrf = await poshmarkCsrf(deadline);
    const ids = [];
    for (const photo of p.photos) ids.push(await client.photo(id, await nativePhoto(msg.job_id, photo, deadline)));
    const journal = (await chrome.storage.local.get(key))[key] || {};
    await chrome.storage.local.set({ [key]: { ...journal, poshmark_post_id: id, poshmark_update_sent: true } });
    stage = 'saving';
    await client.call('POST', `/vm-rest/posts/${id}`, { post: { ...post, cover_shot: { id: ids[0] }, pictures: ids.slice(1).map(photoId => ({ id: photoId })) } },
      { step: 'save', what: 'the update' });
    stage = 'saved';
    const saved = await client.call('GET', `/vm-rest/posts/${id}`, undefined, { step: 'read-back' });
    if (!matches(saved)) throw new Error('Poshmark did not keep this listing\'s title and price. Check the live listing before trying again.');
    return { ok: true, state: 'published', listing_url: poshmarkListingUrl(id) };
  } catch (error) {
    const safe = stage === 'prepare' || (stage === 'saving' && error.rejected === true);
    return marketplaceFailure(error, safe && !recovery);
  }
}

async function runPoshmarkApi(msg, recovery = false) {
  if (msg.payload?.action === 'update') return await updatePoshmarkPost(msg, recovery);
  const p = msg.payload;
  const deadline = Date.now() + 150000;
  const key = `native_operation_${p.operation_id}`;
  let created = null;
  // prepare -> creating -> created -> publishing -> published
  let stage = 'prepare';
  let client = poshmarkClient(deadline, '');
  try {
    if (p.action !== 'list') return marketplaceFailure(marketplaceError('Poshmark listings are created in one step.'), true);
    if (recovery) {
      const journal = (await chrome.storage.local.get(key))[key] || {};
      if (!journal.poshmark_post_id) return marketplaceFailure(new Error('Poshmark did not confirm a listing. Check your Poshmark closet before trying again.'), false);
      const post = await client.call('GET', `/vm-rest/posts/${journal.poshmark_post_id}`, undefined, { step: 'recover' });
      if (post.status === 'published') return { ok: true, state: 'published', listing_url: poshmarkListingUrl(journal.poshmark_post_id) };
      return marketplaceFailure(new Error(`Poshmark has post ${journal.poshmark_post_id} but it is not listed. Check your Poshmark closet before trying again.`), false);
    }
    if (p.confirm !== true) return marketplaceFailure(marketplaceError('Confirm listing on Poshmark.'), true);
    poshmarkPhotoCount(p.photos);
    const account = await poshmarkAccount();
    const post = await poshmarkPreparedPost(p.listing || {}, deadline);
    client = poshmarkClient(deadline, await poshmarkCsrf(deadline));

    stage = 'creating';
    const fresh = await client.call('POST', `/vm-rest/users/${account.user_id}/posts`, {}, { step: 'create', what: 'a new listing' });
    if (!/^[a-f0-9]{24}$/.test(String(fresh.id))) throw marketplaceError('Poshmark did not confirm a new listing.', { code: 'schema_drift', rejected: false });
    created = String(fresh.id);
    stage = 'created';
    const journal = (await chrome.storage.local.get(key))[key] || {};
    await chrome.storage.local.set({ [key]: { ...journal, poshmark_post_id: created } });
    const ids = [];
    for (const photo of p.photos) ids.push(await client.photo(created, await nativePhoto(msg.job_id, photo, deadline)));
    await client.call('POST', `/vm-rest/posts/${created}`, { post: { ...post, cover_shot: { id: ids[0] }, pictures: ids.slice(1).map(id => ({ id })) } },
      { step: 'save', what: 'the listing details' });
    stage = 'publishing';
    // Poshmark's own editor certifies the listing when it publishes.
    await client.call('PUT', `/vm-rest/posts/${created}/status/published?user_certified=true`, {}, { step: 'publish', what: 'to list it' });
    stage = 'published';
    const published = await client.call('GET', `/vm-rest/posts/${created}`, undefined, { step: 'read-back' });
    if (published.status !== 'published') throw new Error('Poshmark has not confirmed the listing.');
    return { ok: true, state: 'published', listing_url: poshmarkListingUrl(created) };
  } catch (error) {
    if (recovery) return marketplaceFailure(error, false);
    // Until the publish request, nothing is public; a clear refusal of it is safe too.
    let safe = stage === 'prepare' || (stage === 'creating' && error.rejected === true)
      || stage === 'created' || (stage === 'publishing' && error.rejected === true);
    if (created && safe) {
      // Remove the unlisted post so a retry starts clean. Runs after a cancel or
      // the deadline too, with its own short limit; only a confirmed removal is safe.
      try {
        await client.call('DELETE', `/vm-rest/posts/${created}?app_type=web`, undefined, { step: 'cleanup', what: 'the cleanup', guard: false, timeoutMs: 10000 });
      } catch (cleanup) {
        marketplaceLog('poshmark', 'cleanup', { error_code: cleanup.code || 'failed' });
        safe = false;
      }
    }
    const result = marketplaceFailure(error, safe);
    if (created && !safe) result.error = `${result.error} Check post ${created} in your Poshmark closet before trying again.`;
    return result;
  }
}
