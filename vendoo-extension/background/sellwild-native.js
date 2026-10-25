// Sellwild drafts live in Anteroom. Review only fills an owned browser form.
// The seller-confirmed Publish action alone clicks Finish, once.
async function sellwildPage(action, payload) {
  let submitted = false;
  try {
    if (location.origin !== 'https://sellwild.com' || location.pathname !== '/sell') throw new Error('Open the prepared Sellwild review form.');
    const active = () => {
      if (Date.now() >= payload.deadline || (payload.operation_id && globalThis.__anteroomCancelledNativeOperation === payload.operation_id)) throw new Error('Sellwild action stopped. Check its result before trying again.');
    };
    const visible = el => Boolean(el?.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
    const field = name => document.getElementById(`${name}-input`);
    const inspect = () => ({ title: field('title')?.value ?? null, description: field('text')?.value ?? null,
      price: field('price')?.value ?? null, category: Boolean(field('category')?.value), condition: Boolean(field('condition')?.value),
      photo_count: new Set([...document.querySelectorAll('img')].filter(visible).map(el => el.src)
        .filter(src => /^(blob:|data:image\/(?:jpeg|jpg|png|webp))/i.test(src))).size });
    if (action === 'inspect') return inspect();
    if (action === 'session') {
      const response = await fetch('/api/auth/session', { credentials: 'include', signal: AbortSignal.timeout(10000) });
      return { signed_in: response.ok && Boolean((await response.json()).user) };
    }
    if (action === 'focus') {
      if (payload.action !== 'review') throw new Error('Only a review action can fill this form.');
      const input = field({ title: 'title', description: 'text', price: 'price' }[payload.field]);
      active();
      if (!input) throw new Error('Sellwild has not loaded its listing fields.');
      input.focus();
      input.setSelectionRange(0, input.value.length);
      return { ok: true };
    }
    if (action === 'photo') {
      if (payload.action !== 'review') throw new Error('Only a review action can prepare photos.');
      const input = document.querySelector('input[type="file"][accept*="image"]');
      if (!input) throw new Error('Sellwild has not loaded its photo control.');
      const bytes = Uint8Array.from(atob(payload.photo.base64), char => char.charCodeAt(0));
      const files = new DataTransfer();
      files.items.add(new File([bytes], payload.photo.name, { type: payload.photo.mime_type }));
      active();
      input.files = files.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return { ok: true };
    }
    if (action === 'preflight' || action === 'publish') {
      if (payload.action !== 'publish' || payload.confirm !== true) throw new Error('Confirm publishing this item on Sellwild.');
      const form = inspect();
      if (form.title !== payload.listing.title || form.description !== String(payload.listing.description || '')
          || Number(String(form.price).replace(/[$,]/g, '')) !== Number(payload.listing.price)
          || form.photo_count !== payload.photo_count) throw new Error('The Sellwild form differs from the saved Anteroom draft. Review it again.');
      if (!form.category || !form.condition || !(Number(payload.listing.price) > 0)) throw new Error('Choose category, condition, and shipping in the Sellwild review form before publishing.');
      const finish = [...document.querySelectorAll('button')].find(el => visible(el) && el.innerText.trim() === 'Finish' && !el.disabled);
      if (!finish) throw new Error('Complete the Sellwild form before publishing.');
      if (action === 'publish') { active(); submitted = true; finish.click(); }
      return { ok: true };
    }
    throw new Error('Unsupported Sellwild action.');
  } catch (error) { return { error: String(error.message || error), safe_to_retry: action === 'publish' && !submitted }; }
}

async function callSellwildPage(tabId, action, payload) {
  if (activeNativeOperation?.cancelled) throw new Error('Marketplace action cancelled.');
  const result = (await chrome.scripting.executeScript({ target: { tabId }, world: 'MAIN', func: sellwildPage, args: [action, payload] }))?.[0]?.result;
  if (!result || result.error) {
    const error = new Error(result?.error || 'Sellwild did not confirm the browser step.');
    error.safeToRetry = result?.safe_to_retry === true;
    throw error;
  }
  return result;
}

function sellwildListingUrl(id, title) {
  if (!/^\d+$/.test(String(id))) return '';
  const slug = String(title).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'untitled';
  return `https://sellwild.com/product/${id}/${slug}`;
}

function observeSellwildPublish(tabId, payload, operationKey) {
  let created;
  let started = false;
  let detached = false;
  let createGranted = false;
  let activationGranted = false;
  const requests = new Map();
  let resolve;
  const result = new Promise(done => { resolve = done; });
  const listener = (source, method, event) => {
    if (source.tabId !== tabId) return;
    if (method === 'Fetch.requestPaused') {
      const url = new URL(event.request.url);
      const writable = !['GET', 'HEAD', 'OPTIONS'].includes(event.request.method);
      let allowed = !writable;
      if (writable && payload.action === 'publish' && payload.confirm === true
          && !activeNativeOperation?.cancelled && Date.now() < payload.deadline) {
        if (url.pathname === '/api/listings/create' && event.request.method === 'POST' && !createGranted) {
          let body;
          try { body = JSON.parse(event.request.postData || '{}'); } catch { /* Block an unrecognized body. */ }
          allowed = body?.title === payload.listing.title && Number(body?.price) === Number(payload.listing.price);
          if (allowed) { createGranted = true; started = true; }
        }
        const id = url.pathname.match(/^\/api\/listings\/(\d+)\/confirm-photos$/)?.[1];
        if (id && event.request.method === 'POST' && created?.id === id && !activationGranted) {
          allowed = true;
          activationGranted = true;
        }
      }
      chrome.debugger.sendCommand({ tabId }, allowed ? 'Fetch.continueRequest' : 'Fetch.failRequest', {
        requestId: event.requestId, ...(allowed ? {} : { errorReason: 'BlockedByClient' }),
      }).catch(() => { detached = true; });
      return;
    }
    if (method === 'Network.requestWillBeSent' && event.request.method === 'POST') {
      const url = new URL(event.request.url);
      if (url.origin !== 'https://sellwild.com') return;
      if (url.pathname === '/api/listings/create') {
        started = true;
        let body;
        try { body = JSON.parse(event.request.postData || '{}'); } catch { return; }
        // This owned form may create only the item and price the seller confirmed.
        if (body.title !== payload.listing.title || Number(body.price) !== Number(payload.listing.price)) return;
        requests.set(event.requestId, { action: 'create' });
      }
      const id = url.pathname.match(/^\/api\/listings\/(\d+)\/confirm-photos$/)?.[1];
      if (id) requests.set(event.requestId, { action: 'activate', id });
    }
    if (method === 'Network.loadingFinished' && requests.has(event.requestId)) {
      const request = requests.get(event.requestId);
      requests.delete(event.requestId);
      chrome.debugger.sendCommand({ tabId }, 'Network.getResponseBody', { requestId: event.requestId }).then(async response => {
        const body = JSON.parse(response.body);
        if (body.success !== true) return;
        if (request.action === 'create' && /^\d+$/.test(String(body.listing?.id))) {
          created = { id: String(body.listing.id), title: body.listing.title || payload.listing.title };
          const journal = (await chrome.storage.local.get(operationKey))[operationKey];
          await chrome.storage.local.set({ [operationKey]: { ...journal, sellwild_created_id: created.id } });
        }
        if (request.action === 'activate' && created?.id === request.id) {
          const receipt = { ok: true, state: body.underReview === true ? 'pending_review' : 'published',
            listing_url: sellwildListingUrl(created.id, created.title) };
          const journal = (await chrome.storage.local.get(operationKey))[operationKey];
          await chrome.storage.local.set({ [operationKey]: { ...journal, sellwild_activation: receipt } });
          resolve(receipt);
        }
      }).catch(() => {});
    }
  };
  const onDetach = source => { if (source.tabId === tabId) detached = true; };
  chrome.debugger.onEvent.addListener(listener);
  chrome.debugger.onDetach.addListener(onDetach);
  return { result, started: () => started, connected: () => !detached, close: async () => {
    chrome.debugger.onEvent.removeListener(listener);
    chrome.debugger.onDetach.removeListener(onDetach);
    await chrome.debugger.sendCommand({ tabId }, 'Fetch.disable').catch(() => {});
  } };
}

async function runSellwildNative(msg, recovery = false) {
  const p = { ...msg.payload, deadline: Date.now() + 120000 };
  if (!/^[a-f0-9]{32}$/.test(String(p.local_draft_id)) || !p.revision_id || !['review', 'publish'].includes(p.action)) {
    return { ok: false, safe_to_retry: true, error: 'Save the Sellwild draft in Anteroom first.' };
  }
  if (p.action === 'publish' && !recovery && p.confirm !== true) return { ok: false, safe_to_retry: true, error: 'Confirm publishing on Sellwild.' };
  const reviewKey = `sellwild_review_${p.local_draft_id}`;
  const operationKey = `native_operation_${p.operation_id}`;
  let review = (await chrome.storage.local.get(reviewKey))[reviewKey];
  let tab;
  let attached = false;
  let clicked = false;
  let observer;
  try {
    if (recovery && p.action === 'publish') {
      const journal = (await chrome.storage.local.get(operationKey))[operationKey];
      return journal?.sellwild_activation || { ok: false, error: 'No confirmed Sellwild activation receipt. Check your Sellwild account before another attempt.' };
    }
    const photoIds = p.photos?.map(photo => photo.id) || p.photo_ids;
    if (review?.revision_id === p.revision_id && (!photoIds || JSON.stringify(photoIds) === JSON.stringify(review.photo_ids))) {
      tab = await chrome.tabs.get(review.tab_id).catch(() => null);
    }
    if (tab && tab.url !== 'https://sellwild.com/sell') tab = null;
    if (p.action === 'publish' || recovery) {
      if (!tab || !review?.ready) {
        return { ok: false, safe_to_retry: p.action === 'review', error: 'Open a new Sellwild review from the saved Anteroom draft.' };
      }
    }
    if (p.action === 'review' && tab && review?.ready) {
      await chrome.tabs.update(tab.id, { active: true });
      if (Number.isInteger(tab.windowId)) await chrome.windows.update(tab.windowId, { focused: true });
      return { ok: true, state: 'draft', review_ready: true };
    }
    if (!tab) {
      tab = await openMarketplaceTab('https://sellwild.com/sell');
      review = { tab_id: tab.id, revision_id: p.revision_id, photo_ids: photoIds, ready: false };
      await chrome.storage.local.set({ [reviewKey]: review });
    }
    if (activeNativeOperation) activeNativeOperation.tabId = tab.id;
    await chrome.tabs.update(tab.id, { active: true });
    if (Number.isInteger(tab.windowId)) await chrome.windows.update(tab.windowId, { focused: true });
    await nativeWait(tab.id, t => t.status === 'complete' && t.url === 'https://sellwild.com/sell', p.deadline);
    if (!(await callSellwildPage(tab.id, 'session', p)).signed_in) throw new Error('Sign in to Sellwild in Chrome before reviewing.');
    await chrome.debugger.attach({ tabId: tab.id }, '1.3');
    attached = true;
    let form;
    do {
      form = await callSellwildPage(tab.id, 'inspect', p);
      if (form.title !== null) break;
      await new Promise(resolve => setTimeout(resolve, 300));
    } while (Date.now() < p.deadline);
    if (p.action === 'review') {
      if (!p.photos?.length || p.photos.length > 20) throw new Error('Choose a supported number of Sellwild photos.');
      if (form.title === p.listing.title && form.description === String(p.listing.description || '') && form.photo_count === p.photos.length) {
        review.prepared_photos = true;
      }
      if (!review.prepared_photos && (form.title || form.description || form.photo_count)) {
        const mismatch = [form.title !== p.listing.title && 'title', form.description !== String(p.listing.description || '') && 'description',
          form.photo_count !== p.photos.length && 'photos'].filter(Boolean);
        throw new Error(`Sellwild restored an unfinished form with different ${mismatch.join(', ')}. Clear that form in Sellwild before reviewing this item.`);
      }
      for (const [index, photo] of (review.prepared_photos ? [] : p.photos).entries()) {
        await callSellwildPage(tab.id, 'photo', { ...p, photo: await nativePhoto(msg.job_id, photo) });
        const photoDeadline = Math.min(p.deadline, Date.now() + 30000);
        do {
          await new Promise(resolve => setTimeout(resolve, 300));
          form = await callSellwildPage(tab.id, 'inspect', p);
        } while (form.photo_count < index + 1 && Date.now() < photoDeadline);
        if (form.photo_count < index + 1) throw new Error(`Sellwild did not prepare photo ${index + 1}. Check its review tab.`);
      }
      review.prepared_photos = true;
      await chrome.storage.local.set({ [reviewKey]: review });
      for (const field of ['title', 'description', 'price']) {
        await callSellwildPage(tab.id, 'focus', { ...p, field });
        await chrome.debugger.sendCommand({ tabId: tab.id }, 'Input.insertText', { text: String(p.listing[field] ?? '') });
      }
      await chrome.debugger.sendCommand({ tabId: tab.id }, 'Input.dispatchKeyEvent', { type: 'keyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
      await chrome.debugger.sendCommand({ tabId: tab.id }, 'Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
      form = await callSellwildPage(tab.id, 'inspect', p);
      const differences = [form.title !== p.listing.title && 'title', form.description !== String(p.listing.description || '') && 'description',
        Number(String(form.price).replace(/[$,]/g, '')) !== Number(p.listing.price) && 'price', form.photo_count !== p.photos.length && 'photos'].filter(Boolean);
      if (differences.length) throw new Error(`Sellwild did not retain the review ${differences.join(', ')}.`);
      await chrome.storage.local.set({ [reviewKey]: { ...review, ready: true, photo_count: p.photos.length } });
      return { ok: true, state: 'draft', review_ready: true };
    }
    p.photo_count = review.photo_count;
    await callSellwildPage(tab.id, 'preflight', p);
    observer = observeSellwildPublish(tab.id, p, operationKey);
    await chrome.debugger.sendCommand({ tabId: tab.id }, 'Network.enable');
    await chrome.debugger.sendCommand({ tabId: tab.id }, 'Fetch.enable', { patterns: [
      { urlPattern: 'https://sellwild.com/api/listings/create', requestStage: 'Request' },
      { urlPattern: 'https://sellwild.com/api/listings/*/edit', requestStage: 'Request' },
      { urlPattern: 'https://sellwild.com/api/listings/*/confirm-photos', requestStage: 'Request' },
    ] });
    clicked = true;
    await callSellwildPage(tab.id, 'publish', p);
    const receipt = await Promise.race([observer.result, new Promise(resolve => setTimeout(() => resolve(null), 60000))]);
    if (receipt) return receipt;
    return { ok: false, safe_to_retry: !observer.started() && observer.connected(), error: observer.started() || !observer.connected()
      ? 'Sellwild has not confirmed activation. Check the result; this publish will not be retried.'
      : 'Sellwild did not submit the form. Complete the required category, condition, account and shipping fields in its review tab.' };
  } catch (error) { return { ok: false, safe_to_retry: (!clicked || error.safeToRetry === true) && !recovery, error: String(error.message || error) }; }
  finally {
    await observer?.close();
    if (attached) await chrome.debugger.detach({ tabId: tab.id }).catch(() => {});
  }
}
