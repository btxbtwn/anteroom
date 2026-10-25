// Independently inspected Depop web request builders (2026-10-24).
// Session material stays in the extension worker. No marketplace tab is used.
async function depopApiAction(action, payload) {
  let submitted = false;
  try {
    const assertActive = () => {
      if (Date.now() >= payload.deadline || activeNativeOperation?.cancelled) {
        throw new Error('The Depop operation stopped. Check its result before trying again.');
      }
    };
    const request = async (method, path, body) => {
      assertActive();
      const allowedRead = method === 'GET' && (/^\/presentation\/api\/v1\/drafts\/[A-Za-z0-9_-]{1,80}\/$/.test(path)
        || path === '/presentation/api/v1/drafts/?limit=24&sort_by=createdAt'
        || /^\/api\/v3\/drafts\/products\/[A-Za-z0-9_-]+\/status\/$/.test(path));
      const draftWrite = payload.action === 'save_draft' && (
        (action === 'create' && method === 'POST' && path === '/presentation/api/v1/drafts/')
        || (action === 'photo' && method === 'POST' && path === '/presentation/api/v1/pictures/')
        || (action === 'save' && method === 'PUT' && /^\/presentation\/api\/v1\/drafts\/[A-Za-z0-9_-]{1,80}\/$/.test(path)));
      const publishWrite = action === 'publish' && payload.action === 'publish' && payload.confirm === true
        && method === 'POST' && path === '/presentation/api/v1/drafts/products/';
      if (!allowedRead && !draftWrite && !publishWrite) throw new Error('Unsupported Depop action.');
      const cookie = await chrome.cookies.get({ url: 'https://www.depop.com/', name: 'access_token' });
      if (!cookie?.value) throw new Error('Sign in to Depop in Chrome first. No tab will be opened automatically.');
      const headers = { Accept: 'application/json', Authorization: `Bearer ${decodeURIComponent(cookie.value)}` };
      await ensureMarketplaceRequestContext();
      if (method !== 'GET') {
        headers['Content-Type'] = 'application/json';
        if (publishWrite) headers['Depop-Session-Id'] = '';
        submitted = true;
      }
      const response = await fetch(`https://webapi.depop.com${path}`, {
        method, headers, credentials: 'omit', signal: AbortSignal.timeout(25000),
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (!response.ok) {
        // Classify the response inside the page. Never export a raw error page,
        // response headers, session material, or arbitrary server messages.
        let failure = '';
        try { failure = (await response.text()).slice(0, 65536); } catch {}
        const reason = /captcha|datadome|verify (?:that )?you(?: are|'re) human|challenge-platform/i.test(failure)
          ? 'Depop requested browser verification.'
          : /csrf|xsrf/i.test(failure) ? 'Depop rejected the request verification.'
          : /token.{0,30}expir|session.{0,30}expir|invalid.{0,15}token/i.test(failure) ? 'Depop reported an invalid or expired session.'
          : /permission denied|insufficient permissions|not authorized|not authorised/i.test(failure) ? 'Depop reported insufficient permission.'
          : 'Depop did not provide a recognized reason.';
        throw new Error(`Depop rejected the ${method === 'GET' ? 'read' : 'request'} (HTTP ${response.status}). ${reason} Check the native draft.`);
      }
      return response.status === 204 ? {} : await response.json();
    };
    const id = String(payload.draft_url || '').match(/\/sellinghub\/drafts\/edit\/([A-Za-z0-9_-]{1,80})\/?$/)?.[1];
    const path = `/presentation/api/v1/drafts/${id}/`;
    const description = [String(payload.listing.title || ''), String(payload.listing.description || '')].filter(Boolean).join('\n\n');
    const matches = draft => draft.description === description
      && (payload.listing.price == null || Number(draft.price_amount) === Number(payload.listing.price));
    const summary = draft => ({ id: String(draft.id), description: draft.description, price: draft.price_amount,
      photo_count: draft.pictures?.length || 0, missing_fields: draft.missing_fields || [] });
    if (action === 'session') {
      await request('GET', '/presentation/api/v1/drafts/?limit=24&sort_by=createdAt');
      return { authenticated: true };
    }
    if (action === 'find') {
      const drafts = await request('GET', '/presentation/api/v1/drafts/?limit=24&sort_by=createdAt');
      const found = (drafts.objects || []).filter(matches);
      if (found.length !== 1 || !/^[A-Za-z0-9_-]{1,80}$/.test(String(found[0].id))) throw new Error('Depop could not identify this draft uniquely. Review drafts in Chrome.');
      return { ...summary(found[0]), draft_url: `https://www.depop.com/sellinghub/drafts/edit/${found[0].id}/` };
    }
    if (action === 'create') {
      const draft = await request('POST', '/presentation/api/v1/drafts/', {
        description, price_amount: String(payload.listing.price ?? ''), price_currency: 'USD', pictures: [], quantity: 1,
      });
      if (!/^[A-Za-z0-9_-]{1,80}$/.test(String(draft.id))) throw new Error('Depop did not confirm its new draft ID. Check drafts before trying again.');
      return { draft_url: `https://www.depop.com/sellinghub/drafts/edit/${draft.id}/` };
    }
    if (!id && action !== 'publish_status') throw new Error('A saved Depop draft link is required.');
    if (action === 'read') return summary(await request('GET', path));
    if (action === 'photo') {
      const signed = await request('POST', '/presentation/api/v1/pictures/', {
        type: 'product', extension: 'jpg', dimensions: { width: 1280, height: 1280 },
      });
      const destination = new URL(signed.url);
      if (destination.protocol !== 'https:' || !/^\d+$/.test(String(signed.id))) throw new Error('Depop did not confirm a photo upload destination.');
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
      assertActive();
      let uploaded;
      try {
        uploaded = await fetch(destination.href, { method: 'PUT', credentials: 'omit',
          headers: { 'Content-Type': 'image/jpeg' }, body: file, signal: AbortSignal.timeout(25000) });
      } catch { throw new Error(`Depop photo upload could not reach ${destination.hostname}. Check the draft before trying again.`); }
      if (!uploaded.ok) throw new Error('Depop did not confirm the photo upload.');
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
      await request('PUT', path, draft);
      const saved = await request('GET', path);
      if (!matches(saved) || saved.pictures?.length !== draft.pictures.length) throw new Error('Depop did not retain the draft fields and photos.');
      return summary(saved);
    }
    if (action === 'prepare_publish' || action === 'publish') {
      if (payload.action !== 'publish' || payload.confirm !== true) throw new Error('Confirm publishing this Depop draft.');
      const draft = await request('GET', path);
      if (!matches(draft) || !draft.pictures?.length || !(Number(draft.price_amount) > 0) || !Array.isArray(draft.missing_fields) || draft.missing_fields.length) {
        throw new Error('Complete and review the Depop draft fields before publishing.');
      }
      if (action === 'prepare_publish') return { ok: true };
      const job = await request('POST', '/presentation/api/v1/drafts/products/', { draft_ids: [id] });
      if (!/^[A-Za-z0-9_-]+$/.test(String(job.id))) throw new Error('Depop did not confirm the publish result. Check its result before trying again.');
      return { publish_job_id: String(job.id) };
    }
    if (action === 'publish_status') {
      if (!/^[A-Za-z0-9_-]+$/.test(String(payload.publish_job_id))) throw new Error('A Depop publish receipt is required.');
      const status = await request('GET', `/api/v3/drafts/products/${payload.publish_job_id}/status/`);
      return { complete: status.count_by_status?.COMPLETE === 1 && status.count_by_status?.PENDING === 0,
        pending: status.count_by_status?.PENDING > 0 };
    }
    throw new Error('Unsupported Depop action.');
  } catch (error) { return { error: String(error.message || error), safe_to_retry: !submitted }; }
}

async function depopAction(action, payload) {
  if (activeNativeOperation?.cancelled) throw new Error('Marketplace action cancelled.');
  const result = await depopApiAction(action, payload);
  if (!result || result.error) {
    const error = new Error(result?.error || 'Depop did not confirm the result.');
    error.safeToRetry = result?.safe_to_retry === true;
    throw error;
  }
  return result;
}

async function runDepopNative(msg, recovery = false) {
  const payload = { ...msg.payload, deadline: Date.now() + 120000 };
  const key = `native_operation_${payload.operation_id}`;
  let committed = recovery;
  try {
    const storeReceipt = async receipt => {
      const journal = (await chrome.storage.local.get(key))[key] || {};
      await chrome.storage.local.set({ [key]: { ...journal, ...receipt } });
    };
    const verify = async () => {
      const draft = await depopAction('read', payload);
      const expected = [payload.listing.title, payload.listing.description].filter(Boolean).join('\n\n');
      if (draft.description !== expected || (payload.listing.price != null && Number(draft.price) !== Number(payload.listing.price))
          || (payload.expected_photo_count != null && draft.photo_count !== payload.expected_photo_count)) {
        throw new Error('The saved Depop draft differs from this revision. Review its fields and photos.');
      }
      return { ok: true, state: 'draft', draft_url: payload.draft_url };
    };
    if (payload.action === 'save_draft') {
      if (recovery) {
        if (!payload.draft_url) {
          const found = await depopAction('find', payload);
          payload.draft_url = found.draft_url;
          await storeReceipt({ receipt_url: found.draft_url });
        }
        const draft = await depopAction('read', payload);
        if (draft.description !== [payload.listing.title, payload.listing.description].filter(Boolean).join('\n\n')
            || (payload.listing.price != null && Number(draft.price) !== Number(payload.listing.price))) {
          throw new Error('The Depop draft differs from this revision. Review it before continuing.');
        }
        if (draft.photo_count === 0) return { ok: false, safe_to_retry: true, draft_url: payload.draft_url,
          error: 'Depop retained the draft before its photos were saved. Save again to finish this same draft.' };
        return await verify();
      }
      if (!payload.draft_url) {
        if (!payload.photos?.length || payload.photos.length > 8) throw new Error('Choose between one and eight Depop photos.');
        committed = true;
        const created = await depopAction('create', payload);
        payload.draft_url = nativeDraftUrl('depop', created.draft_url);
        if (!payload.draft_url) throw new Error('Depop did not return a valid draft link.');
        payload.expected_photo_count = payload.photos.length;
        await storeReceipt({ receipt_url: payload.draft_url, expected_photo_count: payload.expected_photo_count });
      }
      const existing = await depopAction('read', payload);
      if (existing.photo_count === 0) {
        if (!payload.photos?.length || payload.photos.length > 8) throw new Error('Choose between one and eight Depop photos.');
        payload.expected_photo_count = payload.photos.length;
        await storeReceipt({ expected_photo_count: payload.expected_photo_count });
        committed = true;
        payload.uploaded_photo_ids = [];
        for (const photo of payload.photos) {
          const uploaded = await depopAction('photo', { ...payload, photo: await nativePhoto(msg.job_id, photo) });
          payload.uploaded_photo_ids.push(uploaded.id);
        }
      }
      committed = true;
      await depopAction('save', payload);
      return await verify();
    }
    if (!recovery) {
      await depopAction('prepare_publish', payload);
      committed = true;
      const receipt = await depopAction('publish', payload);
      payload.publish_job_id = receipt.publish_job_id;
      await storeReceipt(receipt);
    }
    if (!payload.publish_job_id) throw new Error('No confirmed Depop publish receipt is available. Review the native account.');
    while (Date.now() < payload.deadline) {
      const result = await depopAction('publish_status', payload);
      if (result.complete) return { ok: true, state: 'published', draft_url: payload.draft_url };
      if (!result.pending) throw new Error('Depop did not confirm publishing. Review the native draft.');
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
    throw new Error('Depop has not confirmed publishing yet. Check its result later.');
  } catch (error) { return { ok: false, safe_to_retry: !committed, error: String(error.message || error) }; }
}
