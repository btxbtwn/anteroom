// Poshmark requests run in the extension worker. Only Connect opens a tab.
async function runPoshmarkApi(msg, recovery = false) {
  const p = msg.payload;
  const deadline = Date.now() + 120000;
  let wrote = false;
  let pendingWrite = false;
  let url = p.draft_url;
  const active = () => {
    if (activeNativeOperation?.cancelled || Date.now() >= deadline) throw new Error('Poshmark action stopped. Check its result before trying again.');
  };
  const request = async (method, path, body) => {
    active();
    const write = method !== 'GET';
    if (write && (recovery || (p.action !== 'save_draft' && !(p.action === 'publish' && p.confirm === true)))) {
      throw new Error('Confirm publishing this Poshmark draft.');
    }
    if (write) pendingWrite = true;
    const multipart = body instanceof FormData;
    const response = await fetch(`https://poshmark.com${path}${path.includes('?') ? '&' : '?'}app_version=2.55&pm_version=2026.43.00`, {
      method, credentials: 'include', headers: { Accept: 'application/json', ...(!multipart && write ? { 'Content-Type': 'application/json' } : {}) },
      ...(body === undefined ? {} : { body: multipart ? body : JSON.stringify(body) }),
      signal: AbortSignal.timeout(Math.min(25000, deadline - Date.now())),
    });
    if (!response.ok) {
      if (response.status >= 400 && response.status < 500 && ![408, 409].includes(response.status)) pendingWrite = false;
      throw new Error(`Poshmark rejected the request (HTTP ${response.status}). Reconnect or check the saved draft.`);
    }
    const result = await response.json();
    if (write) { wrote = true; pendingWrite = false; }
    if (result.error || result.data?.error) throw new Error('Poshmark rejected the listing fields.');
    return result.data || result;
  };
  const idFromUrl = () => String(url || '').match(/\/edit-listing\/([a-f0-9]{24})\/?$/)?.[1];
  const read = () => request('GET', `/vm-rest/posts/${idFromUrl()}`);
  const matches = post => post.title === String(p.listing.title) && post.description === String(p.listing.description || '')
    && (p.listing.price == null || Number(post.price_amount?.val) === Number(p.listing.price));
  const privatePost = post => ['draft', 'user_draft', 'unpublished'].includes(post.status);
  const photoCount = post => (post.cover_shot?.id ? 1 : 0) + (post.pictures?.length || 0);
  try {
    if (p.action === 'publish' && p.confirm !== true && !recovery) throw new Error('Confirm publishing this Poshmark draft.');
    if (url && !nativeDraftUrl('poshmark', url)) throw new Error('A valid saved Poshmark draft is required.');
    if (!url) {
      if (recovery || p.action !== 'save_draft') throw new Error('No confirmed Poshmark draft ID is available. Check its result before trying again.');
      if (!p.photos?.length || p.photos.length > 16) throw new Error('Choose between one and sixteen Poshmark photos.');
      const cookie = await chrome.cookies.get({ url: 'https://poshmark.com/', name: 'jwt' });
      let account = {};
      try { account = JSON.parse(atob(cookie.value.split('.')[1])); } catch {}
      if (!/^[a-f0-9]{24}$/.test(String(account.user_id))) throw new Error('Reconnect Poshmark first. No tab will be opened automatically.');
      const created = await request('POST', `/vm-rest/users/${account.user_id}/posts`, {});
      if (!/^[a-f0-9]{24}$/.test(String(created.id)) || !privatePost(created)) throw new Error('Poshmark did not confirm a new private draft. Check its result before trying again.');
      url = `https://poshmark.com/edit-listing/${created.id}`;
      const key = `native_operation_${p.operation_id}`;
      const journal = (await chrome.storage.local.get(key))[key] || {};
      await chrome.storage.local.set({ [key]: { ...journal, receipt_url: url } });
    }
    const existing = await read();
    if (recovery) {
      if (!matches(existing)) throw new Error('The saved Poshmark fields differ from this revision.');
      if (p.action === 'publish' && existing.status === 'published') return { ok: true, state: 'published', draft_url: url,
        listing_url: `https://poshmark.com/listing/${encodeURIComponent(existing.title).replace(/%20/g, '-')}-${idFromUrl()}` };
      if (privatePost(existing) && photoCount(existing) > 0) return { ok: true, state: 'draft', draft_url: url };
      throw new Error('Poshmark has no confirmed private draft result yet.');
    }
    if (!privatePost(existing)) throw new Error('This Poshmark item is no longer a private draft.');
    if (p.action === 'publish') {
      if (!matches(existing) || !existing.catalog?.category || !existing.inventory?.size_quantities?.length
          || !existing.condition || !(Number(existing.price_amount?.val) > 0) || !photoCount(existing)) {
        throw new Error('Complete category, size, condition, price and photos before publishing this Poshmark draft.');
      }
      await request('PUT', `/vm-rest/posts/${idFromUrl()}/status/published`, {});
      const saved = await read();
      if (saved.status !== 'published') throw new Error('Poshmark has not confirmed publication. Check the result before trying again.');
      return { ok: true, state: 'published', draft_url: url,
        listing_url: `https://poshmark.com/listing/${encodeURIComponent(saved.title).replace(/%20/g, '-')}-${idFromUrl()}` };
    }
    const post = {};
    for (const key of ['external_source', 'external_source_id', 'catalog', 'inventory', 'price_amount', 'original_price_amount',
      'offer_auto_actions_v2_enabled', 'offer_auto_actions_min_price_amount', 'title', 'description', 'brand', 'condition',
      'cover_shot', 'pictures', 'videos', 'seller_private_info', 'make_available_at', 'style_tags', 'country_of_origin', 'autolist_draft']) {
      if (existing[key] !== undefined) post[key] = existing[key];
    }
    post.colors = (existing.colors || []).map(color => typeof color === 'object' ? color.name : color);
    post.title = String(p.listing.title);
    post.description = String(p.listing.description || '');
    if (p.listing.brand) post.brand = p.listing.brand;
    if (p.listing.sku) post.seller_private_info = { ...post.seller_private_info, sku: p.listing.sku };
    if (p.listing.price != null) post.price_amount = { ...post.price_amount, currency_code: post.price_amount?.currency_code || 'USD', val: String(p.listing.price) };
    if (!photoCount(existing)) {
      if (!p.photos?.length || p.photos.length > 16) throw new Error('Choose between one and sixteen Poshmark photos.');
      const ids = [];
      for (const photo of p.photos) {
        const image = await nativePhoto(msg.job_id, photo);
        const bytes = Uint8Array.from(atob(image.base64), char => char.charCodeAt(0));
        const form = new FormData();
        form.append('file', new Blob([bytes], { type: image.mime_type }), image.name);
        const uploaded = await request('POST', `/api/posts/${idFromUrl()}/media/scratch?app_type=web`, form);
        if (!/^[a-f0-9]{24}$/.test(String(uploaded.id))) throw new Error('Poshmark did not confirm the photo upload.');
        ids.push(uploaded.id);
      }
      post.cover_shot = { id: ids[0] };
      post.pictures = ids.slice(1).map(id => ({ id }));
    }
    await request('POST', `/vm-rest/posts/${idFromUrl()}`, { post });
    await request('PUT', `/vm-rest/posts/${idFromUrl()}/status/user_draft`, { post });
    const saved = await read();
    if (!privatePost(saved) || !matches(saved) || !photoCount(saved)
        || (!p.draft_url && photoCount(saved) !== p.photos.length)) throw new Error('Poshmark did not retain the private draft fields and photos.');
    return { ok: true, state: 'draft', draft_url: url };
  } catch (error) { return { ok: false, safe_to_retry: !wrote && !pendingWrite, error: String(error.message || error) }; }
}
