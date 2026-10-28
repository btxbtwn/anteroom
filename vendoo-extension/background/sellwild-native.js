// Sellwild publishing and live edits from the extension worker, with no marketplace tab.
// Sellwild has no drafts: its own sell page creates the listing, uploads the
// photos to the presigned S3 URLs it gets back, then confirm-photos activates
// it (read from sellwild.com's web client, 2026-10-24). Its edit page
// (/account/my-listings/[id], build TfctsWXpff2fKS, 2026-10-26) saves with
// PUT /api/listings/{id}/edit. Only a seller-confirmed Publish or Update runs
// these, once; a lost reply is never retried.
const SELLWILD = 'https://sellwild.com';
const SELLWILD_MAX_PHOTOS = 20;
const SELLWILD_PHOTO_EDGE = 1280;
const SELLWILD_MAX_PRICE = 999999;
// Sellwild's sell page flags any side of 108 inches or more.
const SELLWILD_MAX_SIDE_IN = 108;
// Vehicle leaves take a VIN and mileage instead of a package (Sellwild's client, 2026-10-25).
const SELLWILD_VEHICLES = new Set([43, 95, 157, 158, 159, 160, 161, 162, 163, 164, 291, 292]);

function sellwildListingUrl(id, title) {
  if (!/^\d+$/.test(String(id))) return '';
  const slug = String(title).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'untitled';
  return `${SELLWILD}/product/${id}/${slug}`;
}

// Weight in pounds, as Sellwild's "Weight (lbs)" field takes it: the Sellwild tab's
// own weight, otherwise the package in Item Details.
function sellwildWeightLb(listing) {
  const own = Number(listing.sellwild_specifics?.package?.weight);
  const pounds = own > 0 ? own : Number(listing.weight_lb || 0) + Number(listing.weight_oz || 0) / 16;
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

// The address Sellwild's sell page shows under Shipping From: its own list reversed,
// the default one, otherwise the newest. Changing it happens in Sellwild's Address Book.
function sellwildShipFrom(addresses) {
  const list = (Array.isArray(addresses) ? addresses : []).slice().reverse();
  return list.find(entry => Number(entry.isDefault) === 1 || entry.isDefault === true) || list[0] || null;
}

// The Shipping From card as the sell page prints it.
function sellwildShipFromText(addresses) {
  const address = sellwildShipFrom(addresses);
  if (!address?.line1) return '';
  return [address.fullName, address.line1, address.line2, `${address.locality}, ${address.region}, ${address.zipCode}`,
    'United States of America'].map(line => String(line || '').trim()).filter(Boolean).join('\n');
}

function sellwildAddress(addresses) {
  const address = sellwildShipFrom(addresses);
  if (!address?.line1 || !address.locality || !address.region || !address.zipCode) return null;
  const latitude = Number(address.latitude);
  const longitude = Number(address.longitude);
  return { locationText: `${address.locality}, ${address.region}`,
    ...(address.latitude != null && Number.isFinite(latitude) && address.longitude != null && Number.isFinite(longitude) ? { latitude, longitude } : {}) };
}

// The create body, limited to the fields Sellwild's own create schema accepts.
function sellwildCreateBody(listing, choice, address, photoCount) {
  const vehicle = SELLWILD_VEHICLES.has(Number(choice.category_id));
  const specifics = listing.sellwild_specifics || {};
  // Sellwild's own client sends 0 for both outside vehicle categories.
  return { title: String(marketplaceCopy(listing, 'sellwild', 'title') || '').trim(), text: String(marketplaceCopy(listing, 'sellwild', 'description') || ''),
    price: Number(listing.price), categoryId: Number(choice.category_id), itemCondition: Number(choice.condition_id),
    ...address, shippable: true, parcelTemplate: vehicle ? '' : choice.parcel_template || '',
    ...(vehicle ? {} : { ...(sellwildWeightLb(listing) ? { weight: sellwildWeightLb(listing) } : {}),
      ...(choice.package_dimensions || sellwildBox(listing)) }),
    vin: vehicle ? String(specifics.vin || '').trim() : 0, mileage: vehicle ? String(specifics.mileage || '').trim() : 0,
    photos: photoCount };
}

// What Sellwild would refuse in this listing, before anything is sent.
function sellwildMissing(listing, choice, box) {
  const vehicle = SELLWILD_VEHICLES.has(Number(choice.category_id));
  const specifics = listing.sellwild_specifics || {};
  const title = String(marketplaceCopy(listing, 'sellwild', 'title') || '').trim();
  return [(title.length < 3 || title.length > 255) && { field: 'title', needs: 'a title of 3 to 255 characters' },
    !(Number(listing.price) >= 5 && Number(listing.price) <= SELLWILD_MAX_PRICE) && { field: 'price', needs: 'a price from $5 to $999,999' },
    !(Number.isInteger(Number(choice.category_id)) && Number(choice.category_id) > 0) && { field: 'sellwild_specifics.nativeCategoryId', needs: 'a Sellwild category' },
    !(Number.isInteger(Number(choice.condition_id)) && Number(choice.condition_id) > 0) && { field: 'sellwild_specifics.nativeConditionId', needs: 'a Sellwild condition' },
    vehicle && !String(specifics.vin || '').trim() && { field: 'sellwild_specifics.vin', needs: "the vehicle's VIN" },
    vehicle && !String(specifics.mileage || '').trim() && { field: 'sellwild_specifics.mileage', needs: "the vehicle's mileage" },
    !vehicle && !choice.fixed_package && !sellwildWeightLb(listing) && { field: 'weight', needs: 'a package weight' },
    !vehicle && !box && { field: 'dimensions', needs: 'package dimensions' },
    !vehicle && box && Math.max(box.depth, box.width, box.height) >= SELLWILD_MAX_SIDE_IN
      && { field: 'dimensions', needs: `a package under ${SELLWILD_MAX_SIDE_IN} inches on every side` }].filter(Boolean);
}

async function runSellwildNative(msg, recovery = false) {
  const p = { ...msg.payload, deadline: Date.now() + 150000 };
  const key = `native_operation_${p.operation_id}`;
  const storeReceipt = async receipt => {
    const journal = (await chrome.storage.local.get(key))[key] || {};
    await chrome.storage.local.set({ [key]: { ...journal, ...receipt } });
  };
  if (p.action === 'update') return await updateSellwildListing(p, recovery);
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
    const box = p.sellwild?.package_dimensions || sellwildBox(listing);
    const missing = [...sellwildMissing(listing, choice, box),
      ...(!photos.length || photos.length > SELLWILD_MAX_PHOTOS ? [{ field: 'photos', needs: `between 1 and ${SELLWILD_MAX_PHOTOS} photos` }] : [])];
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
    // The sell page applies a chosen coupon after the listing is live. No coupon
    // means no request. A failed discount leaves the listing up.
    const couponIds = [...new Set((Array.isArray(choice.coupon_ids) ? choice.coupon_ids : []).map(Number)
      .filter(couponId => Number.isInteger(couponId) && couponId > 0))];
    if (couponIds.length) {
      try {
        const applied = await sellwildFetch(`/api/listings/${id}/coupons`, { method: 'POST', deadline: p.deadline, step: 'coupons',
          body: { couponIds } });
        if (applied?.success === false) throw new Error('discount');
      } catch {
        receipt.warning = 'Your listing is live, but the discount was not added. Add it from Discounts.';
      }
    }
    await storeReceipt({ sellwild_activation: receipt });
    return receipt;
  } catch (error) {
    const safe = stage === 'prepare' || (stage === 'creating' && error.rejected === true);
    const result = marketplaceFailure(error, safe);
    if (created) result.error = `${result.error} Sellwild listing ${created.id} exists but is not active; check your Sellwild listings before another attempt.`;
    return result;
  }
}

// The fields Sellwild's edit page compares to name what changed. Its edit
// schema requires title, price and categoryId; the rest are sent only when
// they change. Leaving out ``photo`` keeps the photos already on the listing.
const SELLWILD_EDIT_FIELDS = ['title', 'text', 'price', 'categoryId', 'itemCondition', 'shippable', 'weight', 'height', 'width', 'depth', 'parcelTemplate'];
const SELLWILD_EDIT_REQUIRED = ['title', 'price', 'categoryId'];
const SELLWILD_EDIT_LABELS = { title: 'title', text: 'description', price: 'price', categoryId: 'category', itemCondition: 'condition',
  shippable: 'shipping', weight: 'package weight', height: 'package size', width: 'package size', depth: 'package size',
  parcelTemplate: 'package', vin: 'VIN', mileage: 'mileage' };
const SELLWILD_LIVE_READ_WAITS_MS = [0, 700, 1500];

function sellwildSame(key, a, b) {
  if (key === 'shippable') return (a === true || Number(a) === 1) === (b === true || Number(b) === 1);
  if (['title', 'text', 'parcelTemplate', 'vin', 'mileage'].includes(key)) return marketplaceText(String(a ?? '')) === marketplaceText(String(b ?? ''));
  const empty = value => value == null || value === '';
  return empty(a) || empty(b) ? empty(a) && empty(b) : Number(a) === Number(b);
}

function sellwildLiveFields(listing) {
  return { title: listing.title, text: listing.text ?? listing.description, price: listing.price,
    categoryId: listing.categoryId ?? listing.category?.id, itemCondition: listing.itemCondition, shippable: listing.shippable,
    weight: listing.weight, height: listing.height, width: listing.width, depth: listing.depth, parcelTemplate: listing.parcelTemplate,
    vin: listing.vin, mileage: listing.mileage };
}

function sellwildDifferences(live, wanted) {
  return [...new Set(Object.keys(wanted).filter(key => !sellwildSame(key, live[key], wanted[key])).map(key => SELLWILD_EDIT_LABELS[key] || key))];
}

async function updateSellwildListing(p, recovery) {
  const id = String(p.listing_id || '');
  if (!/^[1-9]\d{0,14}$/.test(id)) return marketplaceFailure(marketplaceError('A Sellwild listing link is required before updating.'), true);
  const listing = p.listing || {};
  const choice = p.sellwild || {};
  const vehicle = SELLWILD_VEHICLES.has(Number(choice.category_id));
  const { photos: _count, vin, mileage, ...form } = sellwildCreateBody(listing, choice, {}, 0);
  const wanted = Object.fromEntries(Object.entries({ ...form, ...(vehicle ? { vin, mileage } : {}) })
    .filter(([key]) => SELLWILD_EDIT_FIELDS.includes(key) || (vehicle && ['vin', 'mileage'].includes(key))));
  const read = async () => {
    const body = await sellwildFetch(`/api/listings/${id}`, { deadline: p.deadline, step: 'read' });
    if (String(body?.listing?.id ?? '') !== id) throw marketplaceError('Sellwild did not return this listing.', { code: 'schema_drift' });
    return body.listing;
  };
  let stage = 'prepare';
  try {
    if (recovery) {
      // A lost reply's context has the listing, not the category choice.
      const differ = sellwildDifferences(sellwildLiveFields(await read()),
        Object.fromEntries(['title', 'text', 'price'].map(key => [key, wanted[key]])));
      if (!differ.length) return { ok: true, state: 'published', listing_url: sellwildListingUrl(id, wanted.title) };
      return marketplaceFailure(marketplaceError(`Sellwild has not confirmed the update (${differ.join(', ')} still differ). Check the live listing.`,
        { code: 'uncertain', rejected: false }), false);
    }
    if (p.confirm !== true) throw marketplaceError('Confirm updating the live Sellwild listing.');
    const missing = sellwildMissing(listing, choice, p.sellwild?.package_dimensions || sellwildBox(listing));
    if (missing.length) throw marketplaceMissing('sellwild', missing);
    const session = await sellwildFetch('/api/auth/session', { deadline: p.deadline, step: 'session' });
    if (!session?.user) throw marketplaceError('Sign in to Sellwild: reconnect it in Anteroom. Updating will not open a tab.', { code: 'signed_out' });
    const current = await read();
    if (String(current.user?.id ?? current.userId ?? '') !== String(session.user.id)) {
      throw marketplaceError('This Sellwild listing belongs to another account. Nothing was changed.');
    }
    if (Number(current.status) !== 1) throw marketplaceError('This Sellwild listing is not live. Nothing was changed.');
    const live = sellwildLiveFields(current);
    const changed = Object.keys(wanted).filter(key => !sellwildSame(key, live[key], wanted[key]));
    if (!changed.length) return { ok: true, state: 'published', listing_url: sellwildListingUrl(id, wanted.title) };
    const body = Object.fromEntries(Object.entries(wanted).filter(([key]) => SELLWILD_EDIT_REQUIRED.includes(key) || changed.includes(key)));
    stage = 'writing';
    const edit = await sellwildFetch(`/api/listings/${id}/edit`, { method: 'PUT', deadline: p.deadline, step: 'update', body });
    if (edit?.success !== true) {
      throw marketplaceError('Sellwild did not save the edit.', { code: 'rejected', rejected: edit?.success === false,
        marketplace_message: marketplaceBodyMessage(edit) });
    }
    stage = 'written';
    // Fresh reads decide the outcome, with a short wait for Sellwild's read-after-write lag.
    let differ = ['listing'];
    let after = null;
    for (const wait of SELLWILD_LIVE_READ_WAITS_MS) {
      if (wait) {
        if (Date.now() + wait > p.deadline - 2000) break;
        await marketplaceSleep(wait);
      }
      after = await read();
      differ = sellwildDifferences(sellwildLiveFields(after), wanted);
      if (!differ.length) break;
    }
    if (differ.length) {
      throw marketplaceError(`Sellwild did not keep this listing's ${differ.join(', ')}. Check the live listing.`,
        { code: 'uncertain', rejected: false, marketplace_message: `read back differs: ${differ.join(', ')}` });
    }
    const reviewing = edit.underReview === true || Number(after.status) !== 1;
    return { ok: true, state: reviewing ? 'pending_review' : 'published', listing_url: sellwildListingUrl(id, String(after.title || wanted.title)) };
  } catch (error) {
    const safe = !recovery && (stage === 'prepare' || (stage === 'writing' && error.rejected === true));
    return marketplaceFailure(error, safe);
  }
}
