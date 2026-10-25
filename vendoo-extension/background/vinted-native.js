// Publishes on the seller's own Vinted session. Upload is the only control
// that is clicked. Save draft is never used. A lost reply is not retried.

const VINTED_NEW_ITEM = 'https://www.vinted.com/items/new';

function vintedListingUrl(raw) {
  try {
    const url = new URL(String(raw || ''));
    if (url.protocol !== 'https:' || url.hostname !== 'www.vinted.com') return '';
    const match = /^\/items\/(\d+)(?:-[^/]*)?\/?$/.exec(url.pathname);
    return match ? `https://www.vinted.com/items/${match[1]}` : '';
  } catch {
    return '';
  }
}

async function runVintedNative(msg, recover = false) {
  if (recover) {
    return marketplaceFailure(marketplaceError(
      'Chrome has not said whether this Vinted listing was uploaded. Look on Vinted before listing again.',
      { code: 'uncertain', rejected: false }), false);
  }
  const payload = msg.payload || {};
  if (payload.action !== 'publish' || payload.confirm !== true) {
    return marketplaceFailure(marketplaceError('Confirm listing on Vinted.', { code: 'rejected' }), true);
  }
  const listing = payload.listing && typeof payload.listing === 'object' ? payload.listing : {};
  if (!String(listing.title || '').trim()) {
    return marketplaceFailure(marketplaceMissing('vinted', [{ field: 'title', needs: 'a title' }]), true);
  }
  const photos = Array.isArray(payload.photos) ? payload.photos.slice(0, 20) : [];
  if (!photos.length) {
    return marketplaceFailure(marketplaceMissing('vinted', [{ field: 'photos', needs: 'a photo' }]), true);
  }
  const deadline = Date.now() + 110000;
  try {
    const files = [];
    for (const photo of photos) {
      const loaded = await nativePhoto(msg.job_id || payload.operation_id, photo, deadline);
      files.push({ data: loaded.base64, name: loaded.name, type: loaded.mime_type });
    }
    if (activeNativeOperation?.cancelled || Date.now() >= deadline) throw marketplaceStopped('vinted');
    const tab = await chrome.tabs.create({ url: VINTED_NEW_ITEM, active: false });
    if (activeNativeOperation) activeNativeOperation.tabId = tab.id;
    await nativeWait(tab.id, value => value.status === 'complete', deadline);
    let response = null;
    let lastError = null;
    for (let attempt = 0; attempt < 8 && Date.now() < deadline; attempt += 1) {
      if (activeNativeOperation?.cancelled) throw marketplaceStopped('vinted');
      try {
        response = await chrome.tabs.sendMessage(tab.id, { type: 'START_VINTED', confirm: true, data: listing, files });
        lastError = null;
        break;
      } catch (error) {
        lastError = error;
        await new Promise(resolve => setTimeout(resolve, 400));
      }
    }
    if (lastError) {
      return marketplaceFailure(marketplaceError(
        'Vinted\'s listing form was not on the page. Nothing was published.', { code: 'rejected' }), true);
    }
    if (!response?.ok) {
      const text = String(response?.error || 'Vinted did not publish this item. Nothing was published.');
      return marketplaceFailure(marketplaceError(text, { code: /sign in/i.test(text) ? 'signed_out' : 'rejected' }), true);
    }
    let landed = null;
    try {
      landed = await nativeWait(tab.id, value => Boolean(vintedListingUrl(value.url)), deadline);
    } catch {
      landed = null;
    }
    const listingUrl = landed ? vintedListingUrl(landed.url) : '';
    if (!listingUrl) {
      return marketplaceFailure(marketplaceError(
        'Upload was clicked, but Vinted did not open the new listing. Check Vinted before listing again.',
        { code: 'uncertain', rejected: false }), false);
    }
    return { ok: true, state: 'published', listing_url: listingUrl };
  } catch (error) {
    const safe = error?.rejected !== false && error?.code !== 'uncertain';
    return marketplaceFailure(error?.code ? error : marketplaceError(String(error?.message || error), { code: 'rejected' }), safe);
  }
}
