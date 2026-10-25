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
  const name = String(value || '').trim().toLowerCase().replace('gray', 'grey').replace('beige', 'tan');
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

// Depop's names for the fields a draft still lacks, in plain words.
const DEPOP_FIELD_NEEDS = { brand: 'a brand', brand_id: 'a brand', category: 'a category', category_id: 'a category',
  product_type: 'a category', group: 'a category', condition: 'a condition', colour: 'a colour', size: 'a size',
  variants: 'a size', variant_set: 'a size', shipping: 'shipping', shipping_methods: 'shipping', parcel_size: 'a parcel size',
  address: 'a shipping address', age: 'an age', style: 'a style', source: 'a source', gender: 'a gender', pictures: 'photos',
  photos: 'photos', price: 'a price', price_amount: 'a price', description: 'a description', quantity: 'a quantity' };

function depopFieldNeeds(field) {
  return DEPOP_FIELD_NEEDS[field] || String(field).replace(/[_-]+/g, ' ').trim() || 'more details';
}

// Depop's own limit for the description, which carries the title too.
const DEPOP_MAX_DESCRIPTION = 1000;
const DEPOP_PHOTO_HOSTS = ['.s3.amazonaws.com', '.s3-accelerate.amazonaws.com'];

function depopDescription(listing) {
  return [String(listing?.title || ''), String(listing?.description || '')].filter(Boolean).join('\n\n').slice(0, DEPOP_MAX_DESCRIPTION);
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
    const request = async (method, path, body) => {
      const allowedRead = method === 'GET' && (/^\/presentation\/api\/v1\/drafts\/[A-Za-z0-9_-]{1,80}\/$/.test(path)
        || path === '/presentation/api/v1/drafts/?limit=24&sort_by=createdAt'
        || /^\/api\/v3\/drafts\/products\/[A-Za-z0-9_-]+\/status\/$/.test(path));
      const draftWrite = payload.action === 'save_draft' && (
        (action === 'create' && method === 'POST' && path === '/presentation/api/v1/drafts/')
        || (action === 'photo' && method === 'POST' && path === '/presentation/api/v1/pictures/')
        || (action === 'save' && method === 'PUT' && /^\/presentation\/api\/v1\/drafts\/[A-Za-z0-9_-]{1,80}\/$/.test(path)));
      const publishWrite = action === 'publish' && payload.action === 'publish' && payload.confirm === true
        && method === 'POST' && path === '/presentation/api/v1/drafts/products/';
      if (!allowedRead && !draftWrite && !publishWrite) throw marketplaceError('Unsupported Depop action.');
      const cookie = await chrome.cookies.get({ url: 'https://www.depop.com/', name: 'access_token' });
      if (!cookie?.value) throw marketplaceError('Sign in to Depop in Chrome first. No tab will be opened automatically.', { code: 'signed_out' });
      const headers = { Accept: 'application/json', Authorization: `Bearer ${decodeURIComponent(cookie.value)}` };
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
      Object.assign(draft, depopFormFields(payload.listing));
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
      return { ok: true, state: 'draft', draft_url: payload.draft_url };
    };
    const photosChosen = () => {
      if (!payload.photos?.length || payload.photos.length > 8) {
        throw marketplaceMissing('depop', [{ field: 'photos', needs: 'between one and eight photos' }]);
      }
    };
    if (payload.action === 'save_draft') {
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
      }
      const existing = await depopAction('read', payload);
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
    return marketplaceFailure(error, !committed, payload.draft_url ? { draft_url: payload.draft_url } : {});
  }
}
