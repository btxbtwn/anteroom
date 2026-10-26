// A direct List checked against Poshmark's and Depop's own catalogs before anything is written.
// Send to Vendoo still uses marketplace_category_ids. The ids returned here are
// each site's own category, stored beside Vendoo's.

function directIdString(value) {
  return String(value || '').trim();
}

// Poshmark's form carries 24-character ids. Depop's is menswear_tshirts. A check
// for one site must not read the other site's attached form.
function listingForDirectCheck(listing, marketplace) {
  const id = directIdString(listing.form?.overrides?.categoryV2?.id);
  const depop = /^(kidswear|menswear|womenswear|everything-else)_/.test(id);
  const poshmark = /^[a-f0-9]{24}$/.test(id);
  if ((marketplace === 'depop' && poshmark && !depop) || (marketplace === 'poshmark' && depop && !poshmark)) {
    const copy = { ...listing };
    delete copy.form;
    return copy;
  }
  return listing;
}

function poshmarkDirectId(resolved) {
  if (!resolved?.department?.id || !resolved?.category?.id) return '';
  const ids = [resolved.department.id, resolved.category.id];
  if (resolved.leaf && resolved.leaf !== resolved.category.id) ids.push(resolved.leaf);
  return ids.join('/');
}

function depopDirectId(resolved) {
  if (!resolved?.department || !resolved?.productType) return '';
  return `${resolved.department}_${resolved.productType}`;
}

function hasPoshmarkCategory(listing) {
  return Boolean(directIdString(listing.direct_category_ids?.poshmark)
    || directIdString(listing.marketplace_category_ids?.poshmark)
    || poshmarkNames(listing).length);
}

function hasDepopCategory(listing) {
  return Boolean(directIdString(listing.direct_category_ids?.depop)
    || directIdString(listing.marketplace_category_ids?.depop)
    || directIdString(listing.depop_specifics?.categoryId)
    || directIdString(listing.marketplace_categories?.depop));
}

// Categories Chrome resolved, and the size or parcel List would refuse.
function chromeDirectPreview(listing, sources = {}) {
  const categories = {};
  const gaps = { poshmark: [], depop: [] };
  const item = listing && typeof listing === 'object' ? listing : {};
  if (sources.catalog) {
    const poshmarkListing = listingForDirectCheck(item, 'poshmark');
    const category = poshmarkCategory(sources.catalog, poshmarkListing);
    if (category) {
      const id = poshmarkDirectId(category);
      if (id) categories.poshmark = id;
      const size = poshmarkSize(category.category, poshmarkListing, {
        kids: category.department.display === 'Kids', leaf: category.leaf,
      });
      if (!size?.size) {
        const needs = size?.problem || 'a size Poshmark offers in this category';
        gaps.poshmark.push({ field: 'size', message: `Poshmark needs ${needs}.`, blocking: true });
      }
    } else if (hasPoshmarkCategory(poshmarkListing)) {
      gaps.poshmark.push({
        field: 'direct_category_ids.poshmark', message: 'Poshmark needs a category from its own list.', blocking: true,
      });
    }
  }
  if (sources.attributes) {
    const depopListing = listingForDirectCheck(item, 'depop');
    const category = depopCategory(depopListing, sources.attributes);
    if (category) {
      const id = depopDirectId(category);
      if (id) categories.depop = id;
      const size = depopSize(depopListing, category, sources.attributes);
      if (size.problem) {
        gaps.depop.push({ field: 'size', message: `Depop needs ${size.problem}.`, blocking: true });
      }
    } else if (hasDepopCategory(depopListing)) {
      gaps.depop.push({
        field: 'direct_category_ids.depop', message: 'Depop needs a category from its own list.', blocking: true,
      });
    }
  }
  if (Array.isArray(sources.parcels)) {
    const label = directIdString(item.depop_specifics?.parcelSize || item.depop_specifics?.parcel_size);
    const offered = sources.parcels.some(entry => marketplaceNameKey(entry?.title) === marketplaceNameKey(label));
    if (label && !offered) {
      gaps.depop.push({
        field: 'depop_specifics.parcelSize', message: `Depop does not offer "${label}" as a parcel size.`, blocking: true,
      });
    }
  }
  return { categories, gaps };
}

async function handleMarketplaceDirectCheck(msg) {
  const listing = msg.payload?.listing && typeof msg.payload.listing === 'object' ? msg.payload.listing : {};
  const deadline = Date.now() + 20000;
  const sources = {};
  try { sources.catalog = await poshmarkCatalogData(deadline); } catch { /* A missing catalog leaves the size for the next check. */ }
  try { sources.attributes = await depopAttributes(deadline); } catch { /* Same for Depop's category list. */ }
  let parcelsChecked = false;
  const label = directIdString(listing.depop_specifics?.parcelSize || listing.depop_specifics?.parcel_size);
  if (label) {
    try {
      const price = encodeURIComponent(String(listing.price ?? ''));
      const providers = await marketplaceCatalog(`depop:parcels:${price}`, () => depopAccountRead(
        `/api/v1/shipping-providers/?ids=USPS&itemCurrency=USD&itemCost=${price}`, 'parcel sizes', deadline));
      const rows = Array.isArray(providers) ? providers : providers?.objects || [];
      sources.parcels = rows.flatMap(provider => provider?.parcelSizes || provider?.parcel_sizes || []);
      parcelsChecked = true;
    } catch { /* The known parcel titles still apply when Depop's list cannot be read. */ }
  }
  sendBrowserResult(msg, { ok: true, parcels_checked: parcelsChecked, ...chromeDirectPreview(listing, sources) });
}
