// Sellwild publishing from the extension worker, with no marketplace tab.
// Sellwild has no drafts: its own sell page creates the listing, uploads the
// photos to the presigned S3 URLs it gets back, then confirm-photos activates
// it (read from sellwild.com's web client, 2026-10-24). Only a seller-confirmed
// Publish runs this, once; a lost reply is never retried.
const SELLWILD = 'https://sellwild.com';
const SELLWILD_MAX_PHOTOS = 20;
const SELLWILD_PHOTO_EDGE = 1280;

function sellwildListingUrl(id, title) {
  if (!/^\d+$/.test(String(id))) return '';
  const slug = String(title).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'untitled';
  return `${SELLWILD}/product/${id}/${slug}`;
}

// Weight in pounds, as Sellwild's "Weight (lbs)" field takes it.
function sellwildWeightLb(listing) {
  const pounds = Number(listing.weight_lb || 0) + Number(listing.weight_oz || 0) / 16;
  return Number.isFinite(pounds) && pounds > 0 ? Math.ceil(pounds * 100) / 100 : null;
}

// "LxWxH" inches. Sellwild calls length "depth".
function sellwildBox(listing) {
  const sides = String(listing.package_dimensions_in || '').split(/\s*x\s*/i).map(Number);
  if (sides.length !== 3 || sides.some(side => !Number.isFinite(side) || side <= 0)) return null;
  return { depth: sides[0], width: sides[1], height: sides[2] };
}

async function sellwildFetch(path, { method = 'GET', body, deadline }) {
  if (Date.now() >= deadline || activeNativeOperation?.cancelled) {
    throw new Error('The Sellwild action stopped. Check its result before trying again.');
  }
  await ensureMarketplaceRequestContext();
  const response = await fetch(`${SELLWILD}${path}`, { method, credentials: 'include', signal: AbortSignal.timeout(30000),
    headers: { 'content-type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  let result = null;
  try { result = await response.json(); } catch {}
  if (response.status === 401) {
    // Sellwild checks the session before anything else, so nothing was written.
    throw Object.assign(new Error('Sellwild asked to sign in again. Reconnect Sellwild in Anteroom.'), { rejected: true });
  }
  return { response, result };
}

async function sellwildPhotoBlob(photo) {
  const bytes = Uint8Array.from(atob(photo.base64), char => char.charCodeAt(0));
  const image = await createImageBitmap(new Blob([bytes], { type: photo.mime_type }));
  const scale = Math.min(1, SELLWILD_PHOTO_EDGE / Math.max(image.width, image.height));
  const canvas = new OffscreenCanvas(Math.round(image.width * scale), Math.round(image.height * scale));
  const context = canvas.getContext('2d');
  context.fillStyle = '#ffffff';
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  image.close();
  return canvas.convertToBlob({ type: 'image/jpeg', quality: 0.9 });
}

function sellwildAddress(addresses) {
  const list = Array.isArray(addresses) ? addresses : [];
  const address = list.find(entry => Number(entry.isDefault) === 1 || entry.isDefault === true) || list[list.length - 1];
  if (!address?.line1 || !address.locality || !address.region || !address.zipCode) return null;
  const latitude = Number(address.latitude);
  const longitude = Number(address.longitude);
  return { locationText: `${address.locality}, ${address.region}`,
    ...(address.latitude != null && Number.isFinite(latitude) && address.longitude != null && Number.isFinite(longitude) ? { latitude, longitude } : {}) };
}

// The create body, limited to the fields Sellwild's own create schema accepts.
function sellwildCreateBody(listing, choice, address, photoCount) {
  return { title: String(listing.title || '').trim().slice(0, 255), text: String(listing.description || ''),
    price: Number(listing.price), categoryId: Number(choice.category_id), itemCondition: Number(choice.condition_id),
    ...address, shippable: true, parcelTemplate: '', weight: sellwildWeightLb(listing), ...sellwildBox(listing),
    vin: 0, mileage: 0, photos: photoCount };
}

async function runSellwildNative(msg, recovery = false) {
  const p = { ...msg.payload, deadline: Date.now() + 150000 };
  const key = `native_operation_${p.operation_id}`;
  const storeReceipt = async receipt => {
    const journal = (await chrome.storage.local.get(key))[key] || {};
    await chrome.storage.local.set({ [key]: { ...journal, ...receipt } });
  };
  if (p.action !== 'publish') return { ok: false, safe_to_retry: true, error: 'Sellwild drafts are saved in Anteroom.' };
  if (recovery) {
    const journal = (await chrome.storage.local.get(key))[key] || {};
    if (journal.sellwild_activation) return journal.sellwild_activation;
    return { ok: false, error: journal.sellwild_created_id
      ? `Sellwild created listing ${journal.sellwild_created_id} but did not confirm activating it. Check your Sellwild listings before another attempt.`
      : 'Sellwild did not confirm a listing. Check your Sellwild listings before another attempt.' };
  }
  if (p.confirm !== true) return { ok: false, safe_to_retry: true, error: 'Confirm publishing on Sellwild.' };
  let sent = false;
  let created = null;
  try {
    const listing = p.listing || {};
    const photos = p.photos || [];
    const choice = p.sellwild || {};
    const box = sellwildBox(listing);
    const missing = [String(listing.title || '').trim().length < 3 && 'a title of at least 3 characters',
      !String(listing.description || '').trim() && 'a description', !(Number(listing.price) >= 5) && 'a price of $5 or more',
      !Number.isInteger(Number(choice.category_id)) && 'a Sellwild category', !Number.isInteger(Number(choice.condition_id)) && 'a Sellwild condition',
      !sellwildWeightLb(listing) && 'a package weight', !box && 'package dimensions',
      (!photos.length || photos.length > SELLWILD_MAX_PHOTOS) && `between 1 and ${SELLWILD_MAX_PHOTOS} photos`].filter(Boolean);
    if (missing.length) throw new Error(`Sellwild needs ${missing.join(', ')}.`);

    const session = await sellwildFetch('/api/auth/session', { deadline: p.deadline });
    if (!session.response.ok || !session.result?.user) throw new Error('Sign in to Sellwild: reconnect it in Anteroom. Publishing will not open a tab.');
    const addresses = await sellwildFetch('/api/addresses', { deadline: p.deadline });
    const address = addresses.result?.success ? sellwildAddress(addresses.result.addresses) : null;
    if (!address) throw new Error('Add your ship-from address on Sellwild (street, city, state and ZIP) before publishing.');
    const blobs = [];
    for (const photo of photos) blobs.push(await sellwildPhotoBlob(await nativePhoto(msg.job_id, photo)));

    sent = true;
    const create = await sellwildFetch('/api/listings/create', { method: 'POST', deadline: p.deadline,
      body: sellwildCreateBody(listing, choice, address, blobs.length) });
    const id = String(create.result?.listing?.id ?? '');
    if (!create.response.ok || create.result?.success !== true || !/^\d+$/.test(id)) {
      // A clear rejection means nothing was created.
      if (create.result?.success === false && create.response.status >= 400 && create.response.status < 500) sent = false;
      throw new Error(`Sellwild did not create the listing${create.result?.error ? `: ${String(create.result.error).slice(0, 160)}` : '.'}`);
    }
    created = { id, title: String(create.result.listing.title || listing.title) };
    await storeReceipt({ sellwild_created_id: id });

    const urls = Array.isArray(create.result.presignedUrls) ? create.result.presignedUrls : [];
    for (const [index, blob] of blobs.entries()) {
      const target = urls.find(entry => entry.photoIndex === index)?.presignedUrl;
      if (!target || new URL(target).protocol !== 'https:') throw new Error(`Sellwild did not return an upload address for photo ${index + 1}.`);
      if (Date.now() >= p.deadline || activeNativeOperation?.cancelled) throw new Error('The Sellwild action stopped.');
      const upload = await fetch(target, { method: 'PUT', body: blob, headers: { 'content-type': 'image/jpeg' }, signal: AbortSignal.timeout(60000) });
      if (upload.status !== 200) throw new Error(`Sellwild did not accept photo ${index + 1} (HTTP ${upload.status}).`);
    }

    const activate = await sellwildFetch(`/api/listings/${id}/confirm-photos`, { method: 'POST', deadline: p.deadline });
    if (!activate.response.ok || activate.result?.success !== true) {
      throw new Error(`Sellwild did not activate the listing${activate.result?.error ? `: ${String(activate.result.error).slice(0, 160)}` : '.'}`);
    }
    const receipt = { ok: true, state: activate.result.underReview === true ? 'pending_review' : 'published',
      listing_url: sellwildListingUrl(id, created.title) };
    await storeReceipt({ sellwild_activation: receipt });
    return receipt;
  } catch (error) {
    const message = String(error.message || error);
    return { ok: false, safe_to_retry: !sent || (error.rejected === true && !created), error: created
      ? `${message} Sellwild listing ${created.id} exists but is not active; check your Sellwild listings before another attempt.`
      : message };
  }
}
