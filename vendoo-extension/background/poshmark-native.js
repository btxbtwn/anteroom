// Poshmark one-step listing from the extension worker. Only Connect opens a tab.
// The steps are the listing editor's own (read 2026-10-24): create a post,
// upload photos, save every field, publish. The post is never saved as a
// visible draft. Seller-confirmed only, sent once, never retried.
const POSHMARK_CONDITIONS = new Set(['nwt', 'uln', 'ug', 'uf']);
let poshmarkCatalog = null;

async function poshmarkCatalogData(deadline) {
  if (!poshmarkCatalog) {
    const response = await fetch('https://poshmark.com/vm-rest/meta/catalog_v2', { credentials: 'omit',
      headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(Math.min(30000, Math.max(1000, deadline - Date.now()))) });
    if (!response.ok) throw new Error(`Poshmark's catalog did not load (HTTP ${response.status}).`);
    const body = await response.json();
    poshmarkCatalog = body.data || body;
  }
  return poshmarkCatalog;
}

// Anteroom/Vendoo condition labels to Poshmark's own codes.
function poshmarkCondition(listing) {
  const native = String(listing.poshmark_specifics?.condition || '').toLowerCase();
  if (POSHMARK_CONDITIONS.has(native)) return native;
  const text = String(listing.poshmark_specifics?.poshmarkCondition || listing.condition || '').toLowerCase();
  if (/new with tags|\bnwt\b/.test(text) && !/without/.test(text)) return 'nwt';
  if (/new without tags|\bnwot\b|like new|excellent/.test(text)) return 'uln';
  if (/fair|poor/.test(text)) return 'uf';
  if (/good|imperfection|defect|pre-owned|used/.test(text)) return 'ug';
  return null;
}

function poshmarkCategory(catalog, listing) {
  const ids = listing.marketplace_category_objects?.poshmark?.path;
  if (!Array.isArray(ids) || ids.length < 2) return null;
  const department = catalog.catalog.departments.find(entry => entry.id === ids[0]);
  const category = department?.categories.find(entry => entry.id === ids[1]);
  if (!category) return null;
  const feature = ids[2] && ids[2] !== ids[1] ? category.category_features?.find(entry => entry.id === ids[2]) : null;
  if (ids[2] && ids[2] !== ids[1] && !feature) return null;
  return { department, category, catalog: { department: department.id, category: category.id, category_features: feature ? [feature.id] : [] } };
}

// A size within the category's own size sets. The seller's size scale picks
// the set when Anteroom has it; otherwise the first set with an exact match.
function poshmarkSize(category, listing) {
  const specifics = listing.poshmark_specifics || {};
  const wanted = String(specifics[`${category.id}Size`] || specifics.size || listing.size || '').trim();
  const scale = String(specifics[`${category.id}SizeScale`] || '').trim();
  const sets = category.size_sets || [];
  const sizesOf = set => set.size_systems?.us?.sizes || [];
  const all = sets.flatMap(set => sizesOf(set).map(size => ({ set, size })));
  if (all.length && all.every(entry => entry.size.id === 'OS')) return { ...all[0], quantity: true };
  if (!wanted) return null;
  const variants = [wanted, wanted.replace(/\s*\(.*\)\s*$/, '')].map(value => value.toLowerCase());
  const matches = entry => [entry.size.id, entry.size.short, entry.size.display].some(value => variants.includes(String(value || '').toLowerCase()));
  const ordered = scale ? [...all.filter(entry => entry.set.tags?.includes(scale)), ...all] : all;
  return ordered.find(matches) || null;
}

function poshmarkPostFields(catalog, listing) {
  const missing = [];
  const category = poshmarkCategory(catalog, listing);
  if (!category) missing.push('a Poshmark category');
  const size = category ? poshmarkSize(category.category, listing) : null;
  if (category && !size) missing.push('a size Poshmark offers in this category');
  const condition = poshmarkCondition(listing);
  const price = Number(listing.poshmark_specifics?.poshmarkPrice || listing.price);
  if (!(price >= 3)) missing.push('a price of $3 or more');
  const title = String(listing.title || '').trim();
  if (!title) missing.push('a title');
  if (!String(listing.description || '').trim()) missing.push('a description');
  if (missing.length) throw new Error(`Poshmark needs ${missing.join(', ')}.`);
  const colorNames = new Set((catalog.colors || []).map(color => color.name));
  const colors = [listing.poshmark_specifics?.poshmarkColor || listing.primaryColor, listing.secondaryColor]
    .map(value => ({ Grey: 'Gray', Beige: 'Tan' })[value] || value).filter(value => colorNames.has(value));
  const smart = listing.form?.specifics?.smartSell;
  const minimum = Number(smart?.enabled && smart.minPrice) || Number(listing.poshmark_specifics?.smartSellMinimumPrice || listing.poshmark_specifics?.smartSell?.minimumPrice || 0);
  const sku = String(listing.sku || listing.poshmark_specifics?.poshmarkSku || '').trim();
  const cost = Number(listing.poshmark_specifics?.costPrice || listing.costPrice || 0);
  const brand = listing.form?.overrides?.brand || listing.poshmark_specifics?.poshmarkBrand || listing.brand;
  return {
    title: title.slice(0, 80), description: String(listing.description).slice(0, 1500),
    catalog: category.catalog,
    inventory: { status: 'available', multi_item: false, size_quantity_revision: 0, size_quantities: [{
      size_id: size.size.id, size_obj: size.size, size_system: 'us', quantity_available: 1, quantity_sold: 0,
      size_set_tags: size.set.tags || [], seller_inventory_private_info: {} }] },
    price_amount: { val: String(price), currency_code: 'USD', currency_symbol: '$' },
    original_price_amount: { val: '0', currency_code: 'USD', currency_symbol: '$' },
    ...(condition ? { condition } : {}),
    ...(brand ? { brand: String(brand) } : {}),
    colors: [...new Set(colors)].slice(0, 2),
    style_tags: (Array.isArray(listing.poshmark_specifics?.styleTags) ? listing.poshmark_specifics.styleTags : []).slice(0, 3).map(String),
    offer_auto_actions_v2_enabled: minimum > 0 && minimum < price,
    offer_auto_actions_min_price_amount: minimum > 0 && minimum < price ? { val: String(minimum), currency_code: 'USD' } : null,
    ...(sku || cost > 0 ? { seller_private_info: { ...(sku ? { sku } : {}),
      ...(cost > 0 ? { cost_price_amount: { val: String(cost), currency_code: 'USD' } } : {}) } } : {}),
  };
}

function poshmarkListingUrl(id) {
  return `https://poshmark.com/listing/${id}`;
}

async function runPoshmarkApi(msg, recovery = false) {
  const p = msg.payload;
  const deadline = Date.now() + 150000;
  const key = `native_operation_${p.operation_id}`;
  let created = null;
  let published = false;
  const request = async (method, path, body) => {
    if (activeNativeOperation?.cancelled || Date.now() >= deadline) throw new Error('The Poshmark action stopped.');
    const multipart = body instanceof FormData;
    const response = await fetch(`https://poshmark.com${path}${path.includes('?') ? '&' : '?'}app_version=2.55&pm_version=2026.43.00`, {
      method, credentials: 'include', headers: { Accept: 'application/json', ...(!multipart && method !== 'GET' ? { 'Content-Type': 'application/json' } : {}) },
      ...(body === undefined ? {} : { body: multipart ? body : JSON.stringify(body) }),
      signal: AbortSignal.timeout(Math.min(25000, Math.max(1000, deadline - Date.now()))),
    });
    if (!response.ok) throw Object.assign(new Error(`Poshmark rejected the request (HTTP ${response.status}).`), { status: response.status });
    const result = await response.json();
    // Poshmark answers errors with HTTP 200 and an error body.
    if (result.error) {
      const message = String(result.error.userMessage || result.error.errorType || '').slice(0, 160);
      throw Object.assign(new Error(`Poshmark refused it${message ? `: ${message}` : '.'}`), { rejected: true });
    }
    return result.data || result;
  };
  const readStatus = async id => {
    const post = await request('GET', `/vm-rest/posts/${id}`);
    return post.status;
  };
  try {
    if (p.action !== 'list') return { ok: false, safe_to_retry: true, error: 'Poshmark listings are created in one step.' };
    if (recovery) {
      const journal = (await chrome.storage.local.get(key))[key] || {};
      if (!journal.poshmark_post_id) return { ok: false, error: 'Poshmark did not confirm a listing. Check your Poshmark closet before trying again.' };
      if (await readStatus(journal.poshmark_post_id) === 'published') {
        return { ok: true, state: 'published', listing_url: poshmarkListingUrl(journal.poshmark_post_id) };
      }
      return { ok: false, error: `Poshmark has post ${journal.poshmark_post_id} but it is not listed. Check your Poshmark closet before trying again.` };
    }
    if (p.confirm !== true) return { ok: false, safe_to_retry: true, error: 'Confirm listing on Poshmark.' };
    if (!p.photos?.length || p.photos.length > 16) throw new Error('Choose between one and sixteen Poshmark photos.');
    const post = poshmarkPostFields(await poshmarkCatalogData(deadline), p.listing || {});
    const cookie = await chrome.cookies.get({ url: 'https://poshmark.com/', name: 'jwt' });
    let account = {};
    try { account = JSON.parse(atob(cookie.value.split('.')[1])); } catch {}
    if (!/^[a-f0-9]{24}$/.test(String(account.user_id))) throw new Error('Reconnect Poshmark in Anteroom first. No tab will be opened automatically.');

    const fresh = await request('POST', `/vm-rest/users/${account.user_id}/posts`, {});
    if (!/^[a-f0-9]{24}$/.test(String(fresh.id))) throw new Error('Poshmark did not confirm a new listing.');
    created = String(fresh.id);
    const journal = (await chrome.storage.local.get(key))[key] || {};
    await chrome.storage.local.set({ [key]: { ...journal, poshmark_post_id: created } });
    const ids = [];
    for (const photo of p.photos) {
      const image = await nativePhoto(msg.job_id, photo);
      const bytes = Uint8Array.from(atob(image.base64), char => char.charCodeAt(0));
      const form = new FormData();
      form.append('file', new Blob([bytes], { type: image.mime_type }), image.name);
      const uploaded = await request('POST', `/api/posts/${created}/media/scratch?app_type=web`, form);
      if (!/^[a-f0-9]{24}$/.test(String(uploaded.id))) throw new Error('Poshmark did not confirm a photo upload.');
      ids.push(uploaded.id);
    }
    await request('POST', `/vm-rest/posts/${created}`, { post: { ...post, cover_shot: { id: ids[0] }, pictures: ids.slice(1).map(id => ({ id })) } });
    published = true;
    await request('PUT', `/vm-rest/posts/${created}/status/published`, {});
    if (await readStatus(created) !== 'published') throw new Error('Poshmark has not confirmed the listing.');
    return { ok: true, state: 'published', listing_url: poshmarkListingUrl(created) };
  } catch (error) {
    const message = String(error.message || error);
    // Before the publish request nothing is public; a clear refusal of it is safe too.
    const refused = error.rejected === true || (error.status >= 400 && error.status < 500 && ![408, 409].includes(error.status));
    const safe = !published || refused;
    if (created && safe) {
      // Remove the unlisted post so a retry starts clean.
      await request('DELETE', `/vm-rest/posts/${created}?app_type=web`).catch(() => {});
    }
    return { ok: false, safe_to_retry: safe && !recovery, error: created && !safe
      ? `${message} Check post ${created} in your Poshmark closet before trying again.` : message };
  }
}
