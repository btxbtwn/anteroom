// Poshmark's own editor flow. Saving a user_draft never calls published.
// Publishing is seller-confirmed only, sent once, never retried.
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

// The order and grouping Poshmark's listing page shows its categories in
// (departments, categories, and groups such as Global & Traditional Wear).
async function poshmarkCatalogDisplay(deadline) {
  return await marketplaceCatalog('poshmark:catalog_display', () => marketplaceCatalogRead('poshmark', 'category order', async () => {
    const { data } = await marketplaceFetch(`${POSHMARK}/vm-rest/meta/catalog_display`, { marketplace: 'poshmark', step: 'catalog',
      credentials: 'omit', headers: { Accept: 'application/json' }, deadline, timeoutMs: 30000 });
    const display = data?.data?.us?.catalog_display;
    if (!Array.isArray(display?.children)) throw marketplaceError('Poshmark\'s category list changed.', { code: 'schema_drift' });
    return display;
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

async function poshmarkShippingDiscounts(deadline) {
  const account = await poshmarkAccount();
  const rows = await poshmarkClient(deadline, '').call('GET', `/vm-rest/users/${account.user_id}/seller_shipping_discounts/post`, undefined,
    { step: 'shipping-discounts', what: 'shipping discounts' });
  if (!Array.isArray(rows) || rows.some(row => !/^[a-f0-9]{24}$/.test(String(row.id)) || typeof row.label !== 'string')) {
    throw marketplaceError('Poshmark’s shipping discounts changed. Reopen the listing to try again.', { code: 'schema_drift' });
  }
  return rows.filter(row => row.enabled !== false);
}

// Poshmark's own spelling of the brand (brand search, read 2026-10-25). Brand
// is a free string on a post, so a brand Poshmark does not know, or a failed
// search, keeps the seller's spelling.
async function poshmarkBrand(listing, deadline) {
  const specifics = listing?.poshmark_specifics || {};
  const wanted = String(Object.hasOwn(specifics, 'brand') ? specifics.brand ?? ''
    : Object.hasOwn(listing, 'brand') ? listing.brand ?? ''
    : specifics.poshmarkBrand ?? listing.form?.overrides?.brand ?? '').trim();
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
  if (Array.isArray(listing.poshmark_specifics?.styleTags)) return [...new Set(own.map(spelled))].slice(0, 3);
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
  client.video = async (postId, video, jobId, created) => {
    const blob = await nativeVideo(jobId, video, deadline);
    const asset = await client.call('POST', `/vm-rest/posts/${postId}/videos`, {}, { step: 'video', what: 'a new video' });
    if (!/^[a-f0-9]{24}$/.test(String(asset.id))) throw marketplaceError('Poshmark did not confirm the video asset.', { code: 'schema_drift' });
    await created(asset.id);
    const form = new FormData();
    const extension = { 'video/mp4': 'mp4', 'video/quicktime': 'mov', 'video/webm': 'webm' }[video.mime_type] || 'video';
    form.append('file', blob, `${video.id}.${extension}`);
    await client.call('POST', `/api/posts/${postId}/videos/${asset.id}/media`, form, { step: 'video-upload', what: 'the video upload', timeoutMs: 60000 });
    await client.call('POST', `/vm-rest/posts/${postId}/videos/${asset.id}`, { input: { status: 'submitted' } },
      { step: 'video-submit', what: 'the video for processing' });
    return String(asset.id);
  };
  return client;
}

function poshmarkVideoData(payload) {
  const specifics = payload.listing?.poshmark_specifics || {};
  if (!Object.hasOwn(specifics, 'videoId')) return undefined;
  if (!specifics.videoId) return null;
  const video = payload.video || payload.listing?._native_video;
  if (!video || video.id !== specifics.videoId || !(video.duration_seconds > 0 && video.duration_seconds < 15)
      || !(video.size_bytes > 0) || !video.checksum || !String(video.mime_type).startsWith('video/')) {
    throw marketplaceMissing('poshmark', [{ field: 'poshmark_specifics.videoId', needs: 'the selected video, shorter than 15 seconds' }]);
  }
  return video;
}

function poshmarkExpectedVideo(post, payload, journal) {
  const video = poshmarkVideoData(payload);
  if (video === undefined) return;
  if (video === null) { post.videos = []; return; }
  if (!journal?.poshmark_video_id || journal.poshmark_video_checksum !== video.checksum) {
    throw marketplaceError('Poshmark has not confirmed the video for this revision. Check its draft before trying again.');
  }
  post.videos = [{ id: journal.poshmark_video_id }];
}

async function poshmarkSendVideo(client, postId, payload, jobId, post, key) {
  const video = poshmarkVideoData(payload);
  if (video === undefined) return;
  if (video === null) { post.videos = []; return; }
  await client.video(postId, video, jobId, async id => {
    const journal = (await chrome.storage.local.get(key))[key] || {};
    await chrome.storage.local.set({ [key]: { ...journal, poshmark_video_id: id, poshmark_video_checksum: video.checksum } });
  });
  poshmarkExpectedVideo(post, payload, (await chrome.storage.local.get(key))[key]);
}

function poshmarkVideoPending(post, expected) {
  if (!expected.videos?.length) return false;
  const video = post.videos?.find(row => row.id === expected.videos[0].id);
  if (['failed', 'upload_failed'].includes(video?.media?.processing_status)) throw marketplaceError('Poshmark could not process this video. Replace it before listing.');
  return video?.status !== 'ready';
}

async function poshmarkWaitForVideo(client, postId, expected, deadline) {
  if (!expected.videos?.length) return;
  while (Date.now() < deadline - 10000) {
    const saved = await client.call('GET', `/vm-rest/posts/${postId}`, undefined, { step: 'video-processing' });
    if (!poshmarkVideoPending(saved, expected)) return;
    await marketplaceSleep(1000);
  }
  throw marketplaceError('Poshmark is still processing this video. Check its draft before listing.');
}

// Anteroom/Vendoo condition labels to Poshmark's own codes. The condition the
// seller chose on the Poshmark tab wins, then the code Studio's form carries,
// then the listing's own condition.
function poshmarkCondition(listing) {
  const own = String(listing.poshmark_specifics?.condition || '').toLowerCase();
  if (POSHMARK_CONDITIONS.has(own)) return own;
  if (Object.hasOwn(listing.poshmark_specifics || {}, 'condition')) return poshmarkConditionFromText(own);
  const chosen = poshmarkConditionFromText(listing.poshmark_specifics?.poshmarkCondition);
  if (chosen) return chosen;
  const form = String(listing.form?.overrides?.condition || '').toLowerCase();
  if (POSHMARK_CONDITIONS.has(form)) return form;
  return poshmarkConditionFromText(listing.condition);
}

function poshmarkConditionFromText(value) {
  const text = String(value || '').toLowerCase();
  if (/new with tags|\bnwt\b/.test(text) && !/without/.test(text)) return 'nwt';
  if (/new without tags|\bnwot\b|like new|excellent/.test(text)) return 'uln';
  if (/fair|poor/.test(text)) return 'uf';
  if (/good|imperfection|defect|pre-owned|used/.test(text)) return 'ug';
  if (/^new\b|brand new/.test(text)) return 'nwt';
  return null;
}

// The listing's Poshmark colors, up to two: the Poshmark tab's Color picks,
// otherwise General's primary and secondary color.
function poshmarkColors(listing, catalog) {
  const names = new Set((catalog.colors || []).map(color => color.name));
  const specifics = listing.poshmark_specifics || {};
  const picked = Array.isArray(specifics.colors) ? specifics.colors
    : [specifics.poshmarkColor ?? listing.primaryColor, specifics.poshmarkSecondaryColor ?? listing.secondaryColor];
  return [...new Set(picked.map(value => ({ Grey: 'Gray', Beige: 'Tan' })[value] || value).filter(value => names.has(value)))].slice(0, 2);
}

// Poshmark takes whole dollars only. Vendoo rounds to the nearest one.
function poshmarkPrice(listing) {
  return Math.round(Number(listing?.poshmark_specifics?.poshmarkPrice ?? listing?.price));
}

function poshmarkOriginalPrice(listing) {
  return Array.isArray(listing.seller_fields) && listing.seller_fields.includes('poshmark_specifics.originalPrice')
    ? Number(listing.poshmark_specifics?.originalPrice ?? 0) : 0;
}

function poshmarkKey(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

// A department, category, and optional feature that exist in Poshmark's catalog.
// A "Global & Traditional Wear" id is a grouping in Vendoo's tree, not a catalog level.
function poshmarkFromIds(catalog, ids) {
  const clean = (Array.isArray(ids) ? ids : []).map(String).filter(id => id && !id.startsWith('group:'));
  if (clean.length < 2 || clean.length > 3) return null;
  const department = catalog.catalog?.departments?.find(entry => entry.id === clean[0]);
  const category = department?.categories?.find(entry => entry.id === clean[1]);
  if (!category) return null;
  const feature = clean[2] && clean[2] !== clean[1] ? category.category_features?.find(entry => entry.id === clean[2]) : null;
  if (clean[2] && clean[2] !== clean[1] && !feature) return null;
  return { department, category, leaf: feature ? feature.id : category.id,
    catalog: { department: department.id, category: category.id, category_features: feature ? [feature.id] : [] } };
}

function poshmarkNames(listing) {
  const object = listing.marketplace_category_objects?.poshmark;
  const formPath = listing.form?.overrides?.categoryV2?.displayPath;
  const names = Array.isArray(object?.displayPath) && object.displayPath.length ? object.displayPath
    : Array.isArray(formPath) && formPath.length ? formPath : null;
  if (names) return names.map(String);
  return String(listing.marketplace_categories?.poshmark || '').split('>').map(part => part.trim()).filter(Boolean);
}

// The live leaf whose names match the listing's path, when the stored ids are not in the catalog.
function poshmarkByName(catalog, names) {
  if (names.length < 2) return null;
  const department = (catalog.catalog?.departments || []).find(entry => poshmarkKey(entry.display) === poshmarkKey(names[0]));
  if (!department) return null;
  const category = (department.categories || []).find(entry => poshmarkKey(entry.display) === poshmarkKey(names[1]));
  if (!category) return null;
  if (names.length === 2) return poshmarkFromIds(catalog, [department.id, category.id]);
  const feature = (category.category_features || []).find(entry => poshmarkKey(entry.display) === poshmarkKey(names[2]));
  return feature ? poshmarkFromIds(catalog, [department.id, category.id, feature.id]) : null;
}

// Poshmark's own category for a direct list. A stored direct id wins, then ids
// that are still in the live catalog, then the catalog leaf with the same names.
// Vendoo's category ids are left as they are.
function poshmarkCategory(catalog, listing) {
  const direct = listing.direct_category_ids?.poshmark;
  const directIds = typeof direct === 'string' ? direct.split('/') : Array.isArray(direct) ? direct : [];
  const fromDirect = poshmarkFromIds(catalog, directIds);
  // A stored id that already names the leaf wins. An older sub-category value
  // only fills in when that id stopped at the parent category.
  if (fromDirect?.leaf && fromDirect.leaf !== fromDirect.category.id) return fromDirect;
  const found = fromDirect
    || poshmarkFromIds(catalog, listing.form?.overrides?.categoryV2?.path || listing.marketplace_category_objects?.poshmark?.path)
    || poshmarkByName(catalog, poshmarkNames(listing));
  return found && poshmarkWithSubcategory(catalog, found, listing.poshmark_specifics?.subcategory);
}

// The sub-category the seller chose on the Poshmark tab, inside the category
// already found. "None" leaves it off; a name the category lacks changes nothing.
function poshmarkWithSubcategory(catalog, found, wanted) {
  const name = String(wanted || '').trim();
  if (!name) return found;
  const ids = [found.department.id, found.category.id];
  if (/^none$/i.test(name)) return poshmarkFromIds(catalog, ids);
  const feature = (found.category.category_features || []).find(entry => poshmarkKey(entry.display) === poshmarkKey(name));
  return feature ? poshmarkFromIds(catalog, [...ids, feature.id]) : found;
}

const POSHMARK_FIT_LABELS = { petite: 'Petite', plus: 'Plus', 'big_&_tall': 'Big & Tall', maternity: 'Maternity', juniors: 'Juniors' };

// A size inside the category's own size sets, as { set, size } or { problem }.
// An explicit direct-editor size wins over an older imported coded size.
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
  if (Object.hasOwn(specifics, 'sizeSetId')) {
    const selected = sets.find(set => set.id === specifics.sizeSetId);
    if (!selected) return { problem: 'a size group Poshmark offers in this category' };
    const wanted = String(specifics.nativeSizeId || '').trim();
    const found = sizesOf(selected).find(size => size.id === wanted)
      || matchSize(wanted, sizesOf(selected).map(size => ({ size, keys: sizeEntryKeys([size.id, size.display, size.short]) })))[0]?.size;
    return found ? { set: selected, size: found } : { problem: 'a size from the selected Poshmark size group' };
  }
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

// Smart Pricing's lowest accepted offer in whole dollars, or 0 for off. The
// listing's form decides when it has the setting, off included; otherwise the
// Poshmark specifics' minimum. A minimum at or above the price is off.
function poshmarkSmartMinimum(listing, price) {
  if (listing.poshmark_specifics?.availability === 'not_for_sale') return 0;
  const direct = listing.poshmark_specifics?.smartSell;
  if (direct && typeof direct.enabled === 'boolean') {
    const value = direct.enabled ? Math.round(Number(direct.minimumPrice)) : 0;
    return value > 0 && value < price ? value : 0;
  }
  const smart = listing.form?.specifics?.smartSell;
  const minimum = smart && typeof smart === 'object'
    ? (smart.enabled ? Math.round(Number(smart.minPrice)) : 0)
    : Math.round(Number(listing.poshmark_specifics?.smartSellMinimumPrice || listing.poshmark_specifics?.smartSell?.minimumPrice || 0));
  return minimum > 0 && minimum < price ? minimum : 0;
}

// What the saved post says about Smart Pricing, when it differs from what was
// sent. A post that does not say leaves nothing to compare.
function poshmarkSmartWarning(post, saved) {
  if (typeof saved?.offer_auto_actions_v2_enabled !== 'boolean') {
    marketplaceLog('poshmark', 'smart-pricing', { read_back: 'missing' });
    return null;
  }
  const sent = post.offer_auto_actions_v2_enabled ? Number(post.offer_auto_actions_min_price_amount.val) : 0;
  const kept = saved.offer_auto_actions_v2_enabled ? Number(saved.offer_auto_actions_min_price_amount?.val) || 0 : 0;
  if (sent === kept) return null;
  return sent ? `Poshmark did not keep Smart Pricing at a $${sent} minimum. Check it in the listing.`
    : 'Poshmark left Smart Pricing on. Turn it off in the listing if you do not want it.';
}

function poshmarkInventory(category, listing) {
  const rows = listing.poshmark_specifics?.inventoryRows;
  if (Object.hasOwn(listing.poshmark_specifics || {}, 'inventoryRows') && !Array.isArray(rows)) {
    throw marketplaceMissing('poshmark', [{ field: 'poshmark_specifics.inventoryRows', needs: 'an inventory list of sizes and quantities' }]);
  }
  if (!Array.isArray(rows)) {
    const picked = poshmarkSize(category.category, listing, { kids: category.department.display === 'Kids', leaf: category.leaf });
    if (!picked?.size) throw marketplaceMissing('poshmark', [{ field: 'size', needs: picked?.problem || 'a size Poshmark offers in this category' }]);
    return [{ size_id: picked.size.id, size_obj: picked.size, size_system: 'us', quantity_available: Number(listing.quantity ?? 1),
      quantity_sold: 0, size_set_tags: picked.set.tags || [], seller_inventory_private_info: {} }];
  }
  const missing = [];
  if (!rows.length) missing.push({ field: 'poshmark_specifics.inventoryRows', needs: 'at least one size and quantity' });
  const seen = new Set();
  const inventory = rows.map((row, index) => {
    const field = `poshmark_specifics.inventoryRows.${index}`;
    const custom = row?.sizeSetId === 'custom';
    const set = category.category.size_sets?.find(set => set.id === row?.sizeSetId);
    const id = String(row?.sizeId ?? '').trim();
    const size = custom && id ? { id, display: id, display_with_size_set: id, size_system: 'us' }
      : set?.size_systems?.us?.sizes?.find(size => size.id === id);
    if (!size) missing.push({ field: `${field}.sizeId`, needs: 'a size from this category and group, or a nonblank custom size' });
    if (seen.has(id)) missing.push({ field: `${field}.sizeId`, needs: 'a size that is not already in this inventory' });
    seen.add(id);
    const quantity = Number(row?.quantity);
    if (!Number.isSafeInteger(quantity) || quantity < 1) missing.push({ field: `${field}.quantity`, needs: 'a positive whole-number quantity' });
    const sku = String(row?.sku ?? '').trim();
    if (sku.length > 50) missing.push({ field: `${field}.sku`, needs: 'a SKU of 50 characters or fewer' });
    return { size_id: id, size_obj: size, size_system: 'us', quantity_available: quantity, quantity_sold: 0,
      size_set_tags: custom ? [] : set?.tags || [], seller_inventory_private_info: { sku } };
  });
  if (missing.length) throw marketplaceMissing('poshmark', missing);
  return inventory;
}

function poshmarkPostFields(catalog, listing, { brand = '', styleTags = [] } = {}) {
  const missing = [];
  const category = poshmarkCategory(catalog, listing);
  if (!category) missing.push({ field: 'category', needs: 'a Poshmark category' });
  const sizes = category ? poshmarkInventory(category, listing) : [];
  const condition = poshmarkCondition(listing);
  if (!condition) missing.push({ field: 'condition', needs: 'a condition' });
  const price = poshmarkPrice(listing);
  if (!(price >= 3)) missing.push({ field: 'price', needs: 'a price of $3 or more' });
  if (listing.poshmark_specifics?.availability !== 'not_for_sale'
      && listing.poshmark_specifics?.smartSell?.enabled === true && !poshmarkSmartMinimum(listing, price)) {
    missing.push({ field: 'poshmark_specifics.smartSell.minimumPrice', needs: 'a Smart Pricing minimum above $0 and below the listing price' });
  }
  const title = String(marketplaceCopy(listing, 'poshmark', 'title') || '').trim();
  if (!title) missing.push({ field: 'title', needs: 'a title' });
  if (title.length > 80) missing.push({ field: 'title', needs: 'a title of 80 characters or fewer' });
  const description = String(marketplaceCopy(listing, 'poshmark', 'description') || '').trim();
  if (!description) missing.push({ field: 'description', needs: 'a description' });
  if (description.length > 1500) missing.push({ field: 'description', needs: 'a description of 1500 characters or fewer' });
  const quantity = sizes.reduce((total, row) => total + row.quantity_available, 0);
  if (!Number.isSafeInteger(quantity) || quantity < 1) missing.push({ field: 'quantity', needs: 'a positive whole-number quantity' });
  const originalPrice = poshmarkOriginalPrice(listing);
  if (!Number.isSafeInteger(originalPrice) || originalPrice < 0) missing.push({ field: 'originalPrice', needs: 'an original price in whole dollars, or 0 if unknown' });
  const sku = String(listing.poshmark_specifics?.poshmarkSku ?? listing.sku ?? '').trim();
  if (sku.length > 50) missing.push({ field: 'poshmark_specifics.poshmarkSku', needs: 'a SKU of 50 characters or fewer' });
  const cost = Number(listing.cost);
  const hasCost = Object.hasOwn(listing, 'cost');
  const blankCost = listing.cost == null || listing.cost === '';
  if (hasCost && !blankCost && (!Number.isFinite(cost) || cost < 0)) missing.push({ field: 'cost', needs: 'a cost of $0 or more, or a blank cost' });
  if (missing.length) throw marketplaceMissing('poshmark', missing);
  const colors = poshmarkColors(listing, catalog);
  const minimum = poshmarkSmartMinimum(listing, price);
  // The item's cost (Anteroom's share of the box price), in dollars and cents as Poshmark's editor keeps it.
  const privateFields = {
    ...(sku || Object.hasOwn(listing.poshmark_specifics || {}, 'poshmarkSku') ? { sku } : {}),
    ...(hasCost ? { cost_price_amount: blankCost ? null : { val: cost.toFixed(2), currency_code: 'USD', currency_symbol: '$' } } : {}),
    ...(Object.hasOwn(listing.poshmark_specifics || {}, 'sellerNotes')
      ? { other_info: String(listing.poshmark_specifics.sellerNotes ?? '') } : {}),
  };
  const availability = listing.poshmark_specifics?.availability || 'available';
  if (!['available', 'not_for_sale'].includes(availability)) throw marketplaceMissing('poshmark', [{ field: 'poshmark_specifics.availability', needs: 'For Sale or Not For Sale' }]);
  return {
    title, description,
    catalog: category.catalog,
    inventory: { status: availability, multi_item: sizes.length > 1 || quantity > 1, size_quantity_revision: 0, size_quantities: sizes },
    price_amount: { val: String(price), currency_code: 'USD', currency_symbol: '$' },
    original_price_amount: { val: String(originalPrice), currency_code: 'USD', currency_symbol: '$' },
    condition,
    brand,
    colors: [...new Set(colors)].slice(0, 2),
    style_tags: styleTags,
    offer_auto_actions_v2_enabled: minimum > 0,
    offer_auto_actions_min_price_amount: minimum > 0 ? { val: String(minimum), currency_code: 'USD' } : null,
    ...(Object.keys(privateFields).length ? { seller_private_info: privateFields } : {}),
  };
}

// Every catalog read the post needs, before anything is written.
async function poshmarkPreparedPost(listing, deadline) {
  const catalog = await poshmarkCatalogData(deadline);
  const brand = await poshmarkBrand(listing, deadline);
  const styleTags = poshmarkPostStyleTags(listing, await poshmarkStyleTags(deadline));
  const post = poshmarkPostFields(catalog, listing, { brand, styleTags });
  const discountId = listing.poshmark_specifics?.shippingDiscountId;
  if (Object.hasOwn(listing.poshmark_specifics || {}, 'shippingDiscountId')) {
    const discounts = await poshmarkShippingDiscounts(deadline);
    const selected = discountId ? discounts.find(row => row.id === discountId)
      : discounts.find(row => Number(row.discount_amount?.val) === 0);
    if (!selected) throw marketplaceMissing('poshmark', [{ field: 'poshmark_specifics.shippingDiscountId', needs: 'a current shipping discount from Poshmark' }]);
    post.seller_shipping_discount = { id: selected.id };
  }
  return post;
}

function poshmarkListingUrl(id) {
  return `https://poshmark.com/listing/${id}`;
}

function poshmarkDraftUrl(id) {
  return `https://poshmark.com/edit-listing/${id}`;
}

function poshmarkPostDifferences(actual, expected, photoCount) {
  const text = value => marketplaceText(String(value ?? ''));
  const strings = values => (values || []).map(value => String(value?.name ?? value)).sort();
  const sizes = inventory => (inventory?.size_quantities || []).map(row => ({
    id: String(row.size_id ?? row.size_obj?.id ?? ''), quantity: Number(row.quantity_available),
    tags: strings(row.size_set_tags), sku: String(row.seller_inventory_private_info?.sku ?? ''),
  })).sort((a, b) => a.id.localeCompare(b.id));
  const fields = {
    title: [text(actual.title), text(expected.title)],
    description: [text(actual.description), text(expected.description)],
    brand: [text(actual.brand).toLowerCase(), text(expected.brand).toLowerCase()],
    condition: [actual.condition, expected.condition],
    category: [['department', 'category'].map(k => actual.catalog?.[k]), ['department', 'category'].map(k => expected.catalog?.[k])],
    subcategory: [strings(actual.catalog?.category_features), strings(expected.catalog?.category_features)],
    sizes: [sizes(actual.inventory), sizes(expected.inventory)],
    availability: [actual.inventory?.status, expected.inventory?.status],
    colors: [strings(actual.colors), strings(expected.colors)],
    'style tags': [strings(actual.style_tags), strings(expected.style_tags)],
    price: [Number(actual.price_amount?.val), Number(expected.price_amount?.val)],
    'original price': [Number(actual.original_price_amount?.val), Number(expected.original_price_amount?.val)],
    photos: [(actual.cover_shot?.id ? 1 : 0) + (actual.pictures?.length || 0), photoCount],
  };
  if (expected.seller_shipping_discount) fields['shipping discount'] = [actual.seller_shipping_discount?.id, expected.seller_shipping_discount.id];
  if (Object.hasOwn(expected, 'videos')) fields.videos = [(actual.videos || []).map(video => video.id).sort(), expected.videos.map(video => video.id).sort()];
  for (const key of ['sku', 'cost_price_amount', 'other_info']) {
    if (expected.seller_private_info?.[key] !== undefined) {
      const amount = value => value?.val == null || value.val === '' ? null : Number(value.val);
      fields[key] = key === 'cost_price_amount'
        ? [amount(actual.seller_private_info?.[key]), amount(expected.seller_private_info[key])]
        : [String(actual.seller_private_info?.[key] ?? ''), String(expected.seller_private_info[key])];
    }
  }
  return Object.entries(fields).filter(([, pair]) => JSON.stringify(pair[0]) !== JSON.stringify(pair[1])).map(([field]) => field);
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
    const post = await poshmarkPreparedPost(p.listing || {}, deadline);
    poshmarkVideoData(p);
    if (recovery) poshmarkExpectedVideo(post, p, (await chrome.storage.local.get(key))[key]);
    const matches = saved => saved.status === 'published'
      && !poshmarkPostDifferences(saved, post, p.photos?.length ?? p.expected_photo_count).length;
    if (recovery) {
      if (!matches(existing)) return marketplaceFailure(new Error('Poshmark has not confirmed the update. Check the live listing before trying again.'), false);
      const warning = [poshmarkSmartWarning(post, existing), poshmarkVideoPending(existing, post) ? 'Poshmark is still processing the video.' : ''].filter(Boolean).join(' ');
      return { ok: true, state: 'published', listing_url: poshmarkListingUrl(id), ...(warning ? { warning } : {}) };
    }
    poshmarkPhotoCount(p.photos);
    post.inventory.size_quantity_revision = Number(existing.inventory?.size_quantity_revision) || 0;
    client.csrf = await poshmarkCsrf(deadline);
    await poshmarkSendVideo(client, id, p, msg.job_id, post, key);
    const ids = [];
    for (const photo of p.photos) ids.push(await client.photo(id, await nativePhoto(msg.job_id, photo, deadline)));
    const journal = (await chrome.storage.local.get(key))[key] || {};
    await chrome.storage.local.set({ [key]: { ...journal, poshmark_post_id: id, poshmark_update_sent: true } });
    stage = 'saving';
    await client.call('POST', `/vm-rest/posts/${id}`, { post: { ...post, cover_shot: { id: ids[0] }, pictures: ids.slice(1).map(photoId => ({ id: photoId })) } },
      { step: 'save', what: 'the update' });
    stage = 'saved';
    const saved = await client.call('GET', `/vm-rest/posts/${id}`, undefined, { step: 'read-back' });
    if (!matches(saved)) throw new Error('Poshmark did not keep this listing revision. Check the live listing before trying again.');
    const warning = [poshmarkSmartWarning(post, saved), poshmarkVideoPending(saved, post) ? 'Poshmark is still processing the video.' : ''].filter(Boolean).join(' ');
    return { ok: true, state: 'published', listing_url: poshmarkListingUrl(id), ...(warning ? { warning } : {}) };
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
  let existingDraft = false;
  const draftOnly = p.action === 'save_draft';
  // prepare -> creating -> created -> publishing -> published
  let stage = 'prepare';
  let client = poshmarkClient(deadline, '');
  try {
    if (!['list', 'save_draft'].includes(p.action)) return marketplaceFailure(marketplaceError('Unsupported Poshmark action.'), true);
    if (recovery) {
      const journal = (await chrome.storage.local.get(key))[key] || {};
      if (!journal.poshmark_post_id) return marketplaceFailure(new Error('Poshmark did not confirm a listing. Check your Poshmark closet before trying again.'), false);
      const post = await client.call('GET', `/vm-rest/posts/${journal.poshmark_post_id}`, undefined, { step: 'recover' });
      if (draftOnly) {
        const account = await poshmarkAccount();
        const expected = await poshmarkPreparedPost(p.listing || {}, deadline);
        poshmarkExpectedVideo(expected, p, journal);
        if (String(post.creator_id) !== account.user_id || post.status !== 'user_draft'
            || poshmarkPostDifferences(post, expected, p.photos?.length ?? p.expected_photo_count).length) {
          return marketplaceFailure(marketplaceError('Poshmark has not confirmed this draft revision. Check your Poshmark drafts before trying again.'), false);
        }
        const pendingVideo = poshmarkVideoPending(post, expected);
        return { ok: true, state: 'draft', draft_url: poshmarkDraftUrl(journal.poshmark_post_id),
          ...(pendingVideo ? { warning: 'Poshmark is still processing the video. List will wait for it to finish.' } : {}) };
      }
      if (post.status === 'published') return { ok: true, state: 'published', listing_url: poshmarkListingUrl(journal.poshmark_post_id) };
      return marketplaceFailure(new Error(`Poshmark has post ${journal.poshmark_post_id} but it is not listed. Check your Poshmark closet before trying again.`), false);
    }
    if (!draftOnly && p.confirm !== true) return marketplaceFailure(marketplaceError('Confirm listing on Poshmark.'), true);
    poshmarkPhotoCount(p.photos);
    const account = await poshmarkAccount();
    const post = await poshmarkPreparedPost(p.listing || {}, deadline);
    poshmarkVideoData(p);
    client = poshmarkClient(deadline, await poshmarkCsrf(deadline));

    if (p.draft_url) {
      const match = /^https:\/\/poshmark\.com\/edit-listing\/([a-f0-9]{24})$/.exec(p.draft_url);
      if (!match) throw marketplaceError('Invalid native draft link.');
      const held = await client.call('GET', `/vm-rest/posts/${match[1]}`, undefined, { step: 'read-draft' });
      if (String(held.creator_id) !== account.user_id || held.status !== 'user_draft'
          || (held.inventory?.size_quantities || []).some(row => Number(row.quantity_sold) > 0)) {
        throw marketplaceError('This Poshmark post is not your private draft. It was not changed.');
      }
      created = match[1];
      existingDraft = true;
      post.inventory.size_quantity_revision = Number(held.inventory?.size_quantity_revision) || 0;
    } else {
      stage = 'creating';
      const fresh = await client.call('POST', `/vm-rest/users/${account.user_id}/posts`, {}, { step: 'create', what: 'a new listing' });
      if (!/^[a-f0-9]{24}$/.test(String(fresh.id))) throw marketplaceError('Poshmark did not confirm a new listing.', { code: 'schema_drift', rejected: false });
      created = String(fresh.id);
    }
    stage = 'created';
    const journal = (await chrome.storage.local.get(key))[key] || {};
    await chrome.storage.local.set({ [key]: { ...journal, poshmark_post_id: created } });
    await poshmarkSendVideo(client, created, p, msg.job_id, post, key);
    const ids = [];
    for (const photo of p.photos) ids.push(await client.photo(created, await nativePhoto(msg.job_id, photo, deadline)));
    const savedFields = { ...post, cover_shot: { id: ids[0] }, pictures: ids.slice(1).map(id => ({ id })) };
    await client.call('POST', `/vm-rest/posts/${created}`, { post: savedFields },
      { step: 'save', what: 'the listing details' });
    if (draftOnly) {
      stage = 'saving-draft';
      // Current native editor: publishAsDraft uses PUT with postRequestParams.
      await client.call('PUT', `/vm-rest/posts/${created}/status/user_draft`, { post: savedFields },
        { step: 'save-draft', what: 'to save the private draft' });
      const saved = await client.call('GET', `/vm-rest/posts/${created}`, undefined, { step: 'read-back' });
      const differences = poshmarkPostDifferences(saved, post, p.photos.length);
      if (saved.id !== created || String(saved.creator_id) !== account.user_id || saved.status !== 'user_draft' || differences.length) {
        throw marketplaceError(`Poshmark did not keep this draft revision${differences.length ? ` (${differences.join(', ')})` : ''}. Check your Poshmark drafts.`, { code: 'uncertain', rejected: false });
      }
      const pendingVideo = poshmarkVideoPending(saved, post);
      return { ok: true, state: 'draft', draft_url: poshmarkDraftUrl(created),
        ...(pendingVideo ? { warning: 'Poshmark is still processing the video. List will wait for it to finish.' } : {}) };
    }
    const checked = await client.call('GET', `/vm-rest/posts/${created}`, undefined, { step: 'verify-before-publish' });
    const differences = poshmarkPostDifferences(checked, post, p.photos.length);
    if (checked.id !== created || String(checked.creator_id) !== account.user_id
        || !['draft', 'user_draft'].includes(checked.status) || differences.length) {
      throw marketplaceError(`Poshmark’s draft does not match this revision${differences.length ? ` (${differences.join(', ')})` : ''}. It was not published.`);
    }
    await poshmarkWaitForVideo(client, created, post, deadline);
    stage = 'publishing';
    // Poshmark's own editor certifies the listing when it publishes.
    await client.call('PUT', `/vm-rest/posts/${created}/status/published?user_certified=true`, {}, { step: 'publish', what: 'to list it' });
    stage = 'published';
    const published = await client.call('GET', `/vm-rest/posts/${created}`, undefined, { step: 'read-back' });
    if (published.status !== 'published') throw new Error('Poshmark has not confirmed the listing.');
    if (Object.hasOwn(post, 'videos') && poshmarkPostDifferences(published, post, p.photos.length).includes('videos')) {
      throw new Error('Poshmark did not keep the selected video. Check the live listing before trying again.');
    }
    const warning = poshmarkSmartWarning(post, published);
    return { ok: true, state: 'published', listing_url: poshmarkListingUrl(created), ...(warning ? { warning } : {}) };
  } catch (error) {
    if (recovery) return marketplaceFailure(error, false);
    // Until the publish request, nothing is public; a clear refusal of it is safe too.
    let safe = stage === 'prepare' || (stage === 'creating' && error.rejected === true)
      || stage === 'created' || (stage === 'publishing' && error.rejected === true);
    if (existingDraft && stage !== 'prepare') safe = false;
    if (created && safe && !existingDraft) {
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
