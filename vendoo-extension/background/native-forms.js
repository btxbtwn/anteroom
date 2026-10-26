// Read-only form definitions from the same catalogs and resolvers direct List uses.
// No drafts, photos, addresses, or session material are returned by this reader.
function nativeEditorField(path, label, value, extra = {}) {
  return { key: path, path, label, value: value ?? '', required: false, multi: false,
    selection_only: false, options: [], ...extra };
}

function nativeEditorChoice(path, label, value, choices, extra = {}) {
  const rows = choices.map(row => typeof row === 'string' ? { label: row, value: row } : row);
  return nativeEditorField(path, label, value, { selection_only: true, options_source: 'marketplace',
    options: rows.map(row => row.label), option_values: Object.fromEntries(rows.map(row => [row.label, String(row.value)])), ...extra });
}

function nativeEditorBase(listing, { title = true, maxTitle, maxDescription } = {}) {
  return [
    ...(title ? [nativeEditorField('title', 'Title', listing.title, { required: true, max_length: maxTitle })] : []),
    nativeEditorField('description', 'Description', listing.description, { required: true, max_length: maxDescription }),
    nativeEditorField('price', 'Price', listing.price, { required: true, input_type: 'number' }),
  ];
}

function poshmarkEditorForm(listing, catalog, styleTags = []) {
  const resolved = poshmarkCategory(catalog, listing);
  const categoryChoices = [];
  for (const department of catalog.catalog?.departments || []) {
    for (const category of department.categories || []) {
      categoryChoices.push({ label: `${department.display} > ${category.display}`, value: `${department.id}/${category.id}` });
    }
  }
  const category = resolved?.category;
  const nativeSize = category ? poshmarkSize(category, listing, { kids: resolved.department.display === 'Kids', leaf: resolved.leaf }) : null;
  const sets = category?.size_sets || [];
  const pickedSet = sets.find(set => set.id === listing.poshmark_specifics?.sizeSetId) || nativeSize?.set;
  const specifics = listing.poshmark_specifics || {};
  const fields = [
    ...nativeEditorBase(listing, { maxTitle: 80, maxDescription: 1500 }),
    nativeEditorChoice('direct_category_ids.poshmark', 'Category', resolved ? `${resolved.department.id}/${category.id}` : '', categoryChoices,
      { required: true, clears: ['poshmark_specifics.subcategory', 'poshmark_specifics.sizeSetId', 'poshmark_specifics.nativeSizeId'],
        help: 'Changing category clears its sub-category and size selections.' }),
    nativeEditorChoice('poshmark_specifics.subcategory', 'Sub-category',
      specifics.subcategory || (category?.category_features || []).find(row => row.id === resolved?.leaf)?.display || 'None',
      ['None', ...(category?.category_features || []).map(row => row.display)]),
    nativeEditorField('brand', 'Brand', listing.form?.overrides?.brand || listing.brand),
    nativeEditorChoice('poshmark_specifics.condition', 'Condition', poshmarkCondition(listing), [
      { label: 'New With Tags (NWT)', value: 'nwt' }, { label: 'Like New', value: 'uln' },
      { label: 'Good', value: 'ug' }, { label: 'Fair', value: 'uf' },
    ], { required: true }),
    nativeEditorChoice('poshmark_specifics.sizeSetId', 'Size group', pickedSet?.id || '',
      sets.map(set => ({ label: set.name || set.tags?.join(' / ') || set.id, value: set.id })),
      { required: sets.length > 1, clears: ['poshmark_specifics.nativeSizeId'], help: 'Size choices follow this group.' }),
    nativeEditorChoice('poshmark_specifics.nativeSizeId', 'Size', nativeSize?.size?.id || specifics.nativeSizeId || specifics.size || '',
      (pickedSet?.size_systems?.us?.sizes || []).map(size => ({ label: size.display || size.id, value: size.id })), { required: true }),
    nativeEditorChoice('poshmark_specifics.poshmarkColor', 'Primary Color', specifics.poshmarkColor || ({ Grey: 'Gray', Beige: 'Tan' })[listing.primaryColor] || listing.primaryColor,
      (catalog.colors || []).map(color => color.name)),
    nativeEditorChoice('poshmark_specifics.poshmarkSecondaryColor', 'Secondary Color', specifics.poshmarkSecondaryColor || ({ Grey: 'Gray', Beige: 'Tan' })[listing.secondaryColor] || listing.secondaryColor,
      (catalog.colors || []).map(color => color.name)),
    nativeEditorField('poshmark_specifics.styleTags', 'Style Tags', poshmarkPostStyleTags(listing, styleTags),
      { multi: true, max_values: 3, max_length: 25, options: styleTags, options_source: 'marketplace' }),
    nativeEditorField('poshmark_specifics.smartSell.enabled', 'Smart Pricing',
      typeof specifics.smartSell?.enabled === 'boolean' ? specifics.smartSell.enabled : poshmarkSmartMinimum(listing, poshmarkPrice(listing)) > 0,
      { input_type: 'boolean' }),
    nativeEditorField('poshmark_specifics.smartSell.minimumPrice', 'Minimum Price',
      specifics.smartSell?.minimumPrice ?? specifics.smartSellMinimumPrice ?? specifics.minimumPrice ?? listing.form?.specifics?.smartSell?.minPrice ?? '',
      { input_type: 'number', required: specifics.smartSell?.enabled === true }),
    nativeEditorField('cost', 'Cost', listing.cost, { input_type: 'number', locked: true, help: 'Change cost in Item Details.' }),
    nativeEditorField('poshmark_specifics.poshmarkSku', 'SKU', specifics.poshmarkSku ?? listing.sku ?? '', { max_length: 50 }),
    nativeEditorField('quantity', 'Quantity', listing.quantity ?? 1, { input_type: 'number' }),
    nativeEditorField('poshmark_specifics.originalPrice', 'Original Price', poshmarkOriginalPrice(listing), { input_type: 'number' }),
  ];
  return { marketplace: 'poshmark', native: true, known: true, category_id: poshmarkDirectId(resolved), fields,
    notes: ['Only fields supported by direct List are shown. Shipping uses your Poshmark account settings.'] };
}

function depopEditorForm(listing, attributes, parcels = []) {
  const category = depopCategory(listing, attributes);
  const choices = [];
  for (const group of attributes.group || []) {
    for (const department of group.department || []) {
      const departmentName = depopName((attributes.department || []).find(row => row.id === department)) || department;
      for (const product of group.product_types || []) choices.push({
        label: `${departmentName} > ${depopName(group)} > ${depopName(product)}`, value: `${department}_${product.id}`,
      });
    }
  }
  const mapping = (attributes.category_size_mapping || []).find(row => row.department === category?.department && row.product_type === category?.productType);
  const set = (attributes.size_sets || []).find(row => String(row.id) === String(mapping?.size_set_by_region?.US));
  const resolvedSize = category ? depopSize(listing, category, attributes) : null;
  const specifics = listing.depop_specifics || {};
  const posted = depopFormFields(listing, attributes);
  const extraValues = category ? depopAttributeValues(listing, category, attributes).values : {};
  const brands = attributes.brand || attributes.brands || [];
  const fields = [
    ...nativeEditorBase(listing, { title: false, maxDescription: DEPOP_MAX_DESCRIPTION }),
    nativeEditorChoice('direct_category_ids.depop', 'Category', depopDirectId(category), choices,
      { required: true, clears: ['depop_specifics.nativeSizeId', 'depop_specifics.type', 'depop_specifics.attributes'],
        help: 'Changing category clears its size and category attributes.' }),
    nativeEditorChoice('depop_specifics.brand', 'Brand', depopBrandLabel(brands.find(row => row.id === depopBrand(listing, attributes))) || listing.brand,
      [...new Set(brands.map(depopBrandLabel).filter(Boolean))]),
    nativeEditorChoice('depop_specifics.condition', 'Condition', depopCondition(specifics.condition || listing.form?.overrides?.condition || listing.condition),
      [...DEPOP_CONDITIONS].map(value => ({ label: value.replace(/^used_/, '').replace(/_/g, ' ').replace(/^./, c => c.toUpperCase()), value })), { required: true }),
    nativeEditorChoice('primaryColor', 'Primary Color', depopColour(listing.primaryColor),
      [...DEPOP_COLOURS].map(value => ({ label: value.replace(/^./, c => c.toUpperCase()), value }))),
    nativeEditorChoice('secondaryColor', 'Secondary Color', depopColour(listing.secondaryColor),
      [...DEPOP_COLOURS].map(value => ({ label: value.replace(/^./, c => c.toUpperCase()), value }))),
  ];
  if (set) fields.push(nativeEditorChoice('depop_specifics.nativeSizeId', 'Size',
    resolvedSize?.id ?? specifics.nativeSizeId ?? specifics.size ?? '',
    (set.sizes || []).map(row => ({ label: depopName(row), value: row.id })), { required: true }));
  for (const id of category?.attributeIds || []) {
    const attr = (attributes.generic_attributes || []).find(row => row.id === id);
    if (!attr) continue;
    const options = (attr.attributes || []).filter(row => row.status !== 'inactive');
    const legacy = id === 'material' ? specifics.material : id === 'occasion' ? specifics.occasion : /-type$/.test(id) ? specifics.type : [];
    const value = extraValues[id] ?? specifics.attributes?.[id] ?? legacy ?? [];
    fields.push(nativeEditorChoice(`depop_specifics.attributes.${id}`, depopName(attr) || id, value,
      options.map(row => ({ label: depopName(row), value: row.id })),
      { required: Boolean(attr.is_mandatory), multi: true, max_values: Math.max(1, Number(attr.max_selection) || 1) }));
  }
  for (const key of ['age', 'source', 'style']) {
    const attr = !Array.isArray(attributes[key]) && attributes[key]?.attributes ? attributes[key]
      : (attributes.generic_attributes || []).find(row => row.id === key);
    const options = Array.isArray(attributes[key]) ? attributes[key] : attr?.attributes || [];
    fields.push(nativeEditorChoice(`depop_specifics.${key}`, key.replace(/^./, c => c.toUpperCase()), posted[key] || [],
      options.filter(row => row.status !== 'inactive').map(row => ({ label: depopName(row), value: row.id })),
      { multi: true, max_values: Math.max(1, Number(attr?.max_selection) || 3) }));
  }
  fields.push(nativeEditorChoice('depop_specifics.parcelSize', 'Parcel Size', specifics.parcelSize || '',
    parcels.map(row => String(row.title)), { required: true }),
  nativeEditorField('tags', 'Hashtags', listing.tags || [], { multi: true, max_values: DEPOP_MAX_HASHTAGS }),
  nativeEditorField('depop_specifics.sku', 'SKU', specifics.sku ?? listing.sku ?? ''),
  nativeEditorField('quantity', 'Quantity', listing.quantity ?? 1, { input_type: 'number' }));
  return { marketplace: 'depop', native: true, known: true, category_id: depopDirectId(category), fields,
    notes: ['Buyer pays for Depop shipping. Location comes from the ship-from address selected in Settings.',
      ...(!parcels.length ? ['Sign in to Depop to load the seller’s parcel sizes.'] : [])] };
}

function mercariEditorForm(listing, master) {
  const allCategories = mercariCatalogCategories(master);
  const categories = allCategories.filter(row => !row.parent);
  if (master.itemCategories.some(row => Number(row.id) === mercariCategoryId(listing))) {
    listing = mercariCatalogListing(listing, master);
  } else listing = { ...listing, form: { ...listing.form, sizes: [] } };
  const category = mercariCategoryId(listing);
  const size = mercariSize(listing, category);
  const specifics = listing.mercari_specifics || {};
  const sizes = listing.form?.sizes || [];
  const sizeChoices = sizes.map(row => ({ value: row.id, label: sizes.filter(other => other.name === row.name).length > 1
    ? `${row.name} · ${row.group_name || row.id}` : row.name || row.title }));
  const fields = [
    ...nativeEditorBase(listing, { maxTitle: 80, maxDescription: 1000 }),
    nativeEditorChoice('direct_category_ids.mercari', 'Category', category || '', categories,
      { required: true, clears: ['mercari_specifics.nativeSizeId'], help: 'Categories from Mercari. Choose a category with no subcategories.' }),
    nativeEditorChoice('mercari_specifics.brand', 'Brand', mercariBrandRequest(listing).omit
      ? MERCARI_NO_BRAND
      : (master.itemBrands.find(row => marketplaceNameKey(row.name) === marketplaceNameKey(mercariBrandRequest(listing).name))?.name
        || mercariBrandRequest(listing).name || ''),
      [{ label: 'No brand', value: MERCARI_NO_BRAND }, ...master.itemBrands.map(row => ({ label: row.name, value: row.name }))],
      { clears: ['mercari_specifics.noBrand'],
        help: 'No brand lists this on Mercari without a brand. The brand on General stays as it is. A name Mercari does not list is left off.' }),
    nativeEditorField('mercari_specifics.noBrand', 'No brand / Not sure', specifics.noBrand === true, { input_type: 'boolean',
      help: 'Lists on Mercari with no brand. The brand on General stays as it is.' }),
    nativeEditorChoice('mercari_specifics.condition', 'Condition', mercariConditionId(specifics.condition || listing.condition),
      ['New', 'Like New', 'Good', 'Fair', 'Poor'].map((label, index) => ({ label, value: index + 1 })), { required: true }),
  ];
  const heldCategory = listing.form?.overrides?.categoryV2 || listing.marketplace_category_objects?.mercari;
  if (!categories.some(row => String(row.value) === String(category)) && String(heldCategory?.id) === String(category)) {
    const label = (heldCategory.displayPath || []).join(' > ');
    if (label) fields.find(row => row.path === 'direct_category_ids.mercari').option_values[label] = String(category);
  }
  if (listing.form?.sizes?.length || size.none === 'unknown') fields.push(nativeEditorChoice('mercari_specifics.nativeSizeId', 'Size',
    size.id ?? specifics.nativeSizeId ?? specifics.size ?? listing.size ?? '',
    sizeChoices,
    { required: true, help: 'Sizes from Mercari for this category. The same name in two groups is labeled with its group.' }));
  fields.push(nativeEditorChoice('mercari_specifics.sizeType', 'Size type', specifics.sizeType || specifics.sizeGroup || '',
    ['Regular', 'Petite', 'Plus', 'Tall', 'Juniors', 'Maternity', 'Big & Tall'].map(label => ({ label, value: label })),
    { help: 'Used when Mercari lists that size in more than one group. Blank uses the size type on General, then the regular or standard group.' }));
  fields.push(nativeEditorField('weight_oz', 'Packed weight (oz)', mercariWeightOz(listing), { locked: true, help: 'Change the package in Item Details.' }),
    nativeEditorField('package_dimensions_in', 'Package dimensions (in)', listing.package_dimensions_in, { locked: true }),
    nativeEditorChoice('mercari_specifics.shippingPayerId', 'Who pays shipping', specifics.shippingPayerId ?? 1,
      [{ label: 'Buyer', value: 1 }, { label: 'Seller', value: 2 }]),
    nativeEditorChoice('mercari_specifics.shippingClassId', 'Shipping label', listing.form?.specifics?.shipping?.carrierId || '',
      (listing.form?.shipping_classes || []).map(row => ({ label: `${row.carrierDisplayName} · ${row.name} · $${(row.fee / 100).toFixed(2)}`, value: row.id })),
      { required: true, help: 'Live Mercari labels for this package. Change packed weight and dimensions in Item Details.' }),
    nativeEditorField('mercari_specifics.smartPricing.enabled', 'Smart pricing', specifics.smartPricing?.enabled === true, { input_type: 'boolean' }),
    nativeEditorField('mercari_specifics.smartPricing.minimumPrice', 'Floor price', specifics.smartPricing?.minimumPrice ?? '',
      { input_type: 'number', required: specifics.smartPricing?.enabled === true, help: 'The lowest whole-dollar price Smart Pricing may use.' }));
  return { marketplace: 'mercari', native: true, known: true, category_id: String(category || ''), fields,
    notes: ['Mercari prepaid labels only. Review offer settings on Mercari before publishing.'] };
}

async function handleNativeMarketplaceForm(msg) {
  const { marketplace, listing = {} } = msg.payload || {};
  const deadline = Date.now() + 30000;
  try {
    if (marketplace === 'poshmark') {
      const [catalog, tags] = await Promise.all([poshmarkCatalogData(deadline), poshmarkStyleTags(deadline)]);
      sendBrowserResult(msg, { ok: true, form: poshmarkEditorForm(listing, catalog, tags) });
    } else if (marketplace === 'depop') {
      const attributes = await depopAttributes(deadline);
      let parcels = [];
      try {
        const providers = await depopAccountRead(`/api/v1/shipping-providers/?ids=USPS&itemCurrency=USD&itemCost=${encodeURIComponent(String(listing.price || 1))}`, 'parcel sizes', deadline);
        parcels = (Array.isArray(providers) ? providers : providers?.objects || []).flatMap(row => row.parcelSizes || row.parcel_sizes || []);
      } catch { /* Catalog fields remain usable without an account shipping read. */ }
      sendBrowserResult(msg, { ok: true, form: depopEditorForm(listing, attributes, parcels) });
    } else if (marketplace === 'mercari') {
      const master = await mercariCatalogData(deadline);
      const prepared = mercariShippingListing(listing, await mercariShippingClasses(listing, deadline));
      sendBrowserResult(msg, { ok: true, form: mercariEditorForm(prepared, master) });
    }
    else sendBrowserResult(msg, { ok: false, error: 'This marketplace has no Chrome form reader.' });
  } catch (error) {
    sendBrowserResult(msg, { ok: false, error: error.message || 'The marketplace catalog could not be read.' });
  }
}
