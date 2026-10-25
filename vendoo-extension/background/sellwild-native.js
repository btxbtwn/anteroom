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

async function sellwildFetch(path, { method = 'GET', body, deadline, step = path }) {
  const { data } = await marketplaceFetch(`${SELLWILD}${path}`, { marketplace: 'sellwild', step, method, deadline, timeoutMs: 30000,
    headers: { 'content-type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  return data;
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
  if (p.action !== 'publish') return marketplaceFailure(marketplaceError('Sellwild drafts are saved in Anteroom.'), true);
  if (recovery) {
    const journal = (await chrome.storage.local.get(key))[key] || {};
    if (journal.sellwild_activation) return journal.sellwild_activation;
    return marketplaceFailure(new Error(journal.sellwild_created_id
      ? `Sellwild created listing ${journal.sellwild_created_id} but did not confirm activating it. Check your Sellwild listings before another attempt.`
      : 'Sellwild did not confirm a listing. Check your Sellwild listings before another attempt.'), false);
  }
  if (p.confirm !== true) return marketplaceFailure(marketplaceError('Confirm publishing on Sellwild.'), true);
  // prepare -> creating -> created
  let stage = 'prepare';
  let created = null;
  try {
    const listing = p.listing || {};
    const photos = p.photos || [];
    const choice = p.sellwild || {};
    const box = sellwildBox(listing);
    const missing = [String(listing.title || '').trim().length < 3 && { field: 'title', needs: 'a title of at least 3 characters' },
      !String(listing.description || '').trim() && { field: 'description', needs: 'a description' },
      !(Number(listing.price) >= 5) && { field: 'price', needs: 'a price of $5 or more' },
      !Number.isInteger(Number(choice.category_id)) && { field: 'category', needs: 'a Sellwild category' },
      !Number.isInteger(Number(choice.condition_id)) && { field: 'condition', needs: 'a Sellwild condition' },
      !sellwildWeightLb(listing) && { field: 'weight', needs: 'a package weight' },
      !box && { field: 'dimensions', needs: 'package dimensions' },
      (!photos.length || photos.length > SELLWILD_MAX_PHOTOS) && { field: 'photos', needs: `between 1 and ${SELLWILD_MAX_PHOTOS} photos` }].filter(Boolean);
    if (missing.length) throw marketplaceMissing('sellwild', missing);

    const session = await sellwildFetch('/api/auth/session', { deadline: p.deadline, step: 'session' });
    if (!session?.user) throw marketplaceError('Sign in to Sellwild: reconnect it in Anteroom. Publishing will not open a tab.', { code: 'signed_out' });
    const addresses = await sellwildFetch('/api/addresses', { deadline: p.deadline, step: 'addresses' });
    const address = addresses?.success ? sellwildAddress(addresses.addresses) : null;
    if (!address) throw marketplaceMissing('sellwild', [{ field: 'address', needs: 'your ship-from address (street, city, state and ZIP) on Sellwild' }]);
    const blobs = [];
    for (const photo of photos) blobs.push(await sellwildPhotoBlob(await nativePhoto(msg.job_id, photo, p.deadline)));

    stage = 'creating';
    const create = await sellwildFetch('/api/listings/create', { method: 'POST', deadline: p.deadline, step: 'create',
      body: sellwildCreateBody(listing, choice, address, blobs.length) });
    const id = String(create?.listing?.id ?? '');
    if (create?.success !== true || !/^\d+$/.test(id)) {
      throw marketplaceError('Sellwild did not confirm the new listing.', { code: 'schema_drift', rejected: create?.success === false,
        marketplace_message: marketplaceBodyMessage(create) });
    }
    created = { id, title: String(create.listing.title || listing.title) };
    stage = 'created';
    await storeReceipt({ sellwild_created_id: id });

    const urls = Array.isArray(create.presignedUrls) ? create.presignedUrls : [];
    for (const [index, blob] of blobs.entries()) {
      const target = urls.find(entry => entry.photoIndex === index)?.presignedUrl;
      let destination = null;
      try { destination = new URL(target); } catch {}
      if (destination?.protocol !== 'https:') throw marketplaceError(`Sellwild did not return an upload address for photo ${index + 1}.`, { code: 'schema_drift' });
      // A presigned PUT replaces the same object, so it is safe to repeat.
      await marketplaceFetch(destination.href, { marketplace: 'sellwild', step: 'photo-upload', method: 'PUT', credentials: 'omit',
        headers: { 'content-type': 'image/jpeg' }, body: blob, deadline: p.deadline, timeoutMs: 60000, retry: true, expect: 'none' })
        .catch(error => { throw marketplaceStorageRefusal('sellwild', error); });
    }

    const activate = await sellwildFetch(`/api/listings/${id}/confirm-photos`, { method: 'POST', deadline: p.deadline, step: 'activate' });
    if (activate?.success !== true) {
      throw marketplaceError('Sellwild did not activate the listing.', { code: 'rejected', marketplace_message: marketplaceBodyMessage(activate) });
    }
    const receipt = { ok: true, state: activate.underReview === true ? 'pending_review' : 'published',
      listing_url: sellwildListingUrl(id, created.title) };
    await storeReceipt({ sellwild_activation: receipt });
    return receipt;
  } catch (error) {
    const safe = stage === 'prepare' || (stage === 'creating' && error.rejected === true);
    const result = marketplaceFailure(error, safe);
    if (created) result.error = `${result.error} Sellwild listing ${created.id} exists but is not active; check your Sellwild listings before another attempt.`;
    return result;
  }
}
