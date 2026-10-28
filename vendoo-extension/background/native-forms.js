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

function nativeEditorBase(listing, { marketplace, title = true, maxTitle, maxDescription, minPrice, maxPrice, priceStep = 0.01 } = {}) {
  const copy = field => marketplace ? marketplaceCopy(listing, marketplace, field) : listing?.[field];
  const path = field => marketplace ? `${marketplace}_specifics.${field}` : field;
  return [
    ...(title ? [nativeEditorField(path('title'), 'Title', copy('title'), { required: true, max_length: maxTitle })] : []),
    nativeEditorField(path('description'), 'Description', copy('description'), { required: true, max_length: maxDescription }),
    nativeEditorField('price', 'Price', listing.price, { required: true, input_type: 'number', min_value: minPrice, max_value: maxPrice, step: priceStep }),
  ];
}

// The category list as Poshmark's page shows it: departments, then categories in
// the page's order, with a group such as Global & Traditional Wear as one more
// step. A category with narrower choices ends in one of them or None.
function poshmarkCategoryChoices(catalog, display) {
  const departments = new Map((catalog.catalog?.departments || []).map(row => [row.id, row]));
  const choices = [];
  const add = (department, category, path) => {
    const features = category.category_features || [];
    const base = [department.display, ...path, category.display].join(' > ');
    const id = `${department.id}/${category.id}`;
    if (!features.length) { choices.push({ label: base, value: id }); return; }
    // None is a real last step, so a category that has narrower choices is not saved on its own.
    for (const feature of features) choices.push({ label: `${base} > ${feature.display}`, value: `${id}/${feature.id}` });
    choices.push({ label: `${base} > None`, value: id });
  };
  for (const node of display?.children || []) {
    const department = departments.get(node.id);
    if (!department) continue;
    const categories = new Map((department.categories || []).map(row => [row.id, row]));
    for (const child of node.children || []) {
      if (child.type === 'group') {
        for (const leaf of child.children || []) if (categories.has(leaf.id)) add(department, categories.get(leaf.id), [child.display]);
      } else if (categories.has(child.id)) add(department, categories.get(child.id), []);
    }
  }
  return choices;
}

const POSHMARK_CONDITION_CHOICES = [
  ['New With Tags (NWT)', 'nwt', 'Brand new with original tags or packaging'],
  ['Like New', 'uln', 'New without tags or like new'],
  ['Good', 'ug', 'Gently used with minimal signs of wear, to note in description or photos'],
  ['Fair', 'uf', 'Clearly used with visible wear, to note in description or photos'],
];

// Poshmark's create-listing page, read 2026-10-28: each heading with its hint, in
// page order. The price fields are the ones its Listing Price panel shows.
function poshmarkEditorForm(listing, catalog, display, styleTags = [], shippingDiscounts = []) {
  const resolved = poshmarkCategory(catalog, listing);
  const category = resolved?.category;
  const nativeSize = category ? poshmarkSize(category, listing, { kids: resolved.department.display === 'Kids', leaf: resolved.leaf }) : null;
  const sets = category?.size_sets || [];
  const specifics = listing.poshmark_specifics || {};
  const inventoryRows = Array.isArray(specifics.inventoryRows) ? specifics.inventoryRows : [{
    sizeSetId: nativeSize?.set?.id || (nativeSize?.size ? 'custom' : ''), sizeId: nativeSize?.size?.id || '',
    quantity: listing.quantity ?? 1, sku: '',
  }];
  const at = (section, help) => row => ({ ...row, section, ...(help ? { help } : {}) });
  const [title, description, price] = nativeEditorBase(listing, { marketplace: 'poshmark', maxTitle: 80, maxDescription: 1500, minPrice: 3, priceStep: 1 });
  const fields = [
    at('Title', 'Share key details like Brand, Size, and Color.')(title),
    at('Description', 'Tell shoppers about your listing by describing the Brand, Size, Color, Condition, Material, and any other noteworthy details.')(
      { ...description, input_type: 'textarea' }),
    at('Category', 'Add details to help your buyers find your item quickly.')(
      nativeEditorChoice('direct_category_ids.poshmark', 'Category', poshmarkDirectId(resolved), poshmarkCategoryChoices(catalog, display),
        { required: true, clears: ['poshmark_specifics.subcategory', 'poshmark_specifics.sizeSetId', 'poshmark_specifics.nativeSizeId', 'poshmark_specifics.inventoryRows'] })),
    at('Size', 'Select a single or multi item. If you have more than one of the same item for sale, add each size with its quantity.')(
      nativeEditorField('poshmark_specifics.inventoryRows', 'Size', inventoryRows,
        { input_type: 'inventory', required: true, category_specific: true, inventory_groups: [
          ...sets.map(set => ({ id: set.id, label: set.name || set.tags?.join(' / ') || set.id,
            sizes: (set.size_systems?.us?.sizes || []).map(size => ({ id: size.id, label: size.display || size.id })) })),
          { id: 'custom', label: 'Custom', sizes: [] },
        ] })),
    at('Condition', ['Specify the condition of your item.', ...POSHMARK_CONDITION_CHOICES.map(([label, , hint]) => `${label}: ${hint}.`)].join(' '))(
      nativeEditorChoice('poshmark_specifics.condition', 'Condition', poshmarkCondition(listing),
        POSHMARK_CONDITION_CHOICES.map(([label, value]) => ({ label, value })), { required: true })),
    at('Brand', 'Optional. Add details to help your buyers find your item quickly.')(
      nativeEditorField('poshmark_specifics.brand', 'Brand',
        Object.hasOwn(specifics, 'brand') ? specifics.brand : (listing.brand ?? listing.form?.overrides?.brand), { max_length: 50 })),
    at('Color', 'Optional. Select up to 2 colors.')(
      nativeEditorChoice('poshmark_specifics.colors', 'Color', poshmarkColors(listing, catalog),
        (catalog.colors || []).map(color => color.name), { multi: true, max_values: 2 })),
    at('Style Tags', 'Optional. Make it easy for buyers to find your listings by selecting or creating up to 3 tags to describe your item (aesthetic, material, style, etc.)')(
      nativeEditorField('poshmark_specifics.styleTags', 'Style Tags', poshmarkPostStyleTags(listing, styleTags),
        { multi: true, max_values: 3, max_length: 25, options: styleTags, options_source: 'marketplace' })),
    ...[
      { ...price, label: 'Listing Price' },
      nativeEditorField('poshmark_specifics.smartSell.enabled', 'Automate Offers',
        typeof specifics.smartSell?.enabled === 'boolean' ? specifics.smartSell.enabled : poshmarkSmartMinimum(listing, poshmarkPrice(listing)) > 0,
        { input_type: 'boolean', help: 'Sell 50% faster by setting your lowest price.' }),
      nativeEditorField('poshmark_specifics.smartSell.minimumPrice', 'Lowest Price',
        specifics.smartSell?.minimumPrice ?? specifics.smartSellMinimumPrice ?? specifics.minimumPrice ?? listing.form?.specifics?.smartSell?.minPrice ?? '',
        { input_type: 'number', required: true, min_value: 0, step: 1 }),
      nativeEditorField('poshmark_specifics.originalPrice', 'Original Price', poshmarkOriginalPrice(listing),
        { input_type: 'number', min_value: 0, step: 1, help: 'Optional' }),
      nativeEditorChoice('poshmark_specifics.shippingDiscountId', 'Shipping Discount',
        specifics.shippingDiscountId || shippingDiscounts.find(row => Number(row.discount_amount?.val) === 0)?.id || '',
        shippingDiscounts.map(row => ({ label: row.label, value: row.id })), { help: 'Optional' }),
    ].map(at('Price')),
    at('Availability')(nativeEditorChoice('poshmark_specifics.availability', 'Availability', specifics.availability || 'available',
      [{ label: 'For Sale', value: 'available' }, { label: 'Not For Sale', value: 'not_for_sale' }], { required: true })),
    ...[
      nativeEditorField('poshmark_specifics.poshmarkSku', 'Listing SKU', specifics.poshmarkSku ?? listing.sku ?? '', { max_length: 50 }),
      nativeEditorField('cost', 'Cost Price', listing.cost, { input_type: 'number', min_value: 0, step: 0.01 }),
      nativeEditorField('poshmark_specifics.sellerNotes', 'Other Info', specifics.sellerNotes || '', { input_type: 'textarea' }),
    ].map(at('Additional Details (Private)')),
  ];
  return { marketplace: 'poshmark', native: true, known: true, category_id: poshmarkDirectId(resolved), fields };
}

function depopEditorForm(listing, attributes, parcels = []) {
  const category = depopCategory(listing, attributes);
  const choices = [];
  for (const group of attributes.group || []) {
    if (group.status === 'inactive') continue;
    for (const product of group.product_types || []) {
      if (product.status === 'inactive') continue;
      for (const department of product.department || group.department || []) {
        const departmentName = depopName((attributes.department || []).find(row => row.id === department)) || department;
        choices.push({ label: `${departmentName} > ${depopName(group)} > ${depopName(product)}`, value: `${department}_${product.id}` });
      }
    }
  }
  const mapping = (attributes.category_size_mapping || []).find(row => row.department === category?.department && row.product_type === category?.productType);
  const set = (attributes.size_sets || []).find(row => String(row.id) === String(mapping?.size_set_by_region?.US));
  const resolvedSize = category ? depopSize(listing, category, attributes) : null;
  const specifics = listing.depop_specifics || {};
  const posted = depopFormFields(listing, attributes);
  const extraValues = category ? depopAttributeValues(listing, category, attributes).values : {};
  const brands = attributes.brand || attributes.brands || [];
  const live = row => row && row.status !== 'inactive';
  // Condition and Color in the order and wording Depop's catalog gives them, limited to the slugs direct List sends.
  const pageChoices = (definition, known) => (definition?.attributes || [])
    .filter(row => live(row) && known.has(row.id) && depopPageName(row))
    .map(row => ({ label: depopPageName(row), value: row.id }));
  const full = depopDescription(listing);
  const base = depopBaseDescription(listing).slice(0, DEPOP_MAX_DESCRIPTION);
  const hashtags = full.startsWith(base) ? full.slice(base.length).trim() : '';
  // Depop's sell page, top to bottom. Hashtags go at the end of the description,
  // and the page names its field labels in plain English ("Body fit").
  const fields = [
    { ...nativeEditorBase(listing, { marketplace: 'depop', title: false, maxDescription: DEPOP_MAX_DESCRIPTION })[0],
      ...(hashtags ? { help: `List adds the hashtags ${hashtags} to the end.` } : {}) },
    nativeEditorChoice('direct_category_ids.depop', 'Category', depopDirectId(category), choices,
      { required: true, clears: ['depop_specifics.nativeSizeId', 'depop_specifics.type', 'depop_specifics.attributes'] }),
  ];
  for (const id of category?.attributeIds || []) {
    const attr = (attributes.generic_attributes || []).find(row => row.id === id);
    const options = depopShownChoices(attr, category);
    if (!options.length) continue;
    const legacy = id === 'material' ? specifics.material : id === 'occasion' ? specifics.occasion : /-type$/.test(id) ? specifics.type : [];
    const value = extraValues[id] ?? specifics.attributes?.[id] ?? legacy ?? [];
    fields.push(nativeEditorChoice(`depop_specifics.attributes.${id}`, depopPageName(attr) || id, value,
      options.map(row => ({ label: depopPageName(row), value: row.id })),
      { required: Boolean(attr.is_mandatory), multi: true, category_specific: true, max_values: Math.max(1, Number(attr.max_selection) || 1) }));
  }
  fields.push(
    nativeEditorChoice('depop_specifics.brand', 'Brand', depopBrandLabel(brands.find(row => row.id === depopBrand(listing, attributes))) || listing.brand,
      [...new Set(brands.map(depopBrandLabel).filter(Boolean))]),
    nativeEditorChoice('depop_specifics.condition', 'Condition', depopCondition(specifics.condition || listing.form?.overrides?.condition || listing.condition),
      pageChoices(attributes.condition, DEPOP_CONDITIONS), { required: true }),
    nativeEditorChoice('depop_specifics.colour', 'Color', posted.colour || [],
      pageChoices(attributes.colour, DEPOP_COLOURS), { multi: true, max_values: 2 }),
  );
  if (set) fields.push(nativeEditorChoice('depop_specifics.nativeSizeId', 'Size',
    resolvedSize?.id ?? specifics.nativeSizeId ?? specifics.size ?? '',
    (set.sizes || []).map(row => ({ label: depopName(row), value: row.id })), { required: true, category_specific: true }));
  fields.push(
    nativeEditorField('quantity', 'Quantity', listing.quantity ?? 1, { input_type: 'number', min_value: 1, step: 1 }),
    nativeEditorField('depop_specifics.sku', 'SKU', specifics.sku ?? listing.sku ?? '', { max_length: 50 }),
  );
  for (const key of ['source', 'age', 'style']) {
    const attr = !Array.isArray(attributes[key]) && attributes[key]?.attributes ? attributes[key]
      : (attributes.generic_attributes || []).find(row => row.id === key);
    const options = Array.isArray(attributes[key]) ? attributes[key] : attr?.attributes || [];
    fields.push(nativeEditorChoice(`depop_specifics.${key}`, key.replace(/^./, c => c.toUpperCase()), posted[key] || [],
      options.filter(row => row.status !== 'inactive').map(row => ({ label: depopName(row), value: row.id })),
      { multi: true, max_values: Math.max(1, Number(attr?.max_selection) || 3) }));
  }
  const [price] = nativeEditorBase(listing, { title: false, minPrice: 1, maxPrice: 9999999999.99 }).filter(row => row.path === 'price');
  fields.push({ ...price, label: 'Item price' },
  // Off unless the seller turns it on: Depop charges an extra fee on the sale.
  nativeEditorField('depop_specifics.boost', 'Promote your item in search to help it sell faster. Pay an extra 12% fee, only if you sell.',
    specifics.boost === true, { input_type: 'boolean' }),
  nativeEditorChoice('depop_specifics.parcelSize', 'Package size', specifics.parcelSize || '',
    parcels.map(row => String(row.title)), { required: true }),
  nativeEditorField('depop_specifics.worldwideShipping.enabled', 'Offer worldwide shipping', specifics.worldwideShipping?.enabled === true,
    { input_type: 'boolean' }),
  // Always present so turning worldwide shipping on shows the fee without reloading the form.
  nativeEditorField('depop_specifics.worldwideShipping.price', 'Worldwide Shipping',
    specifics.worldwideShipping?.price ?? '', { input_type: 'number', required: true, min_value: 0, max_value: 99.99, step: 0.01 }));
  return { marketplace: 'depop', native: true, known: true, category_id: depopDirectId(category), fields: depopPageLayout(fields),
    notes: !parcels.length ? ['Sign in to Depop to load the seller’s parcel sizes.'] : [] };
}

// Depop's sell page, read 2026-10-27: its headings, the hint under each field,
// Size and Quantity at a quarter of the row each, SKU at half.
function depopPageLayout(fields) {
  const hints = {
    'depop_specifics.colour': 'Select up to two colors',
    'depop_specifics.source': max => `What kind of item is this? Add up to ${max}`,
    'depop_specifics.age': max => `Do you know when this item was made? Add up to ${max}`,
    'depop_specifics.style': max => `Pick relevant tags to describe this item's style. Add up to ${max}`,
    'depop_specifics.parcelSize': "Your selection sets your shipping label and the buyer's shipping cost.",
  };
  const section = path => path === 'depop_specifics.description' ? 'Description'
    : ['depop_specifics.source', 'depop_specifics.age', 'depop_specifics.style'].includes(path) ? 'Enhance your listing'
      : path === 'price' ? 'Item price'
        : path === 'depop_specifics.boost' ? 'Boost your listing'
          : path === 'depop_specifics.parcelSize' || path.startsWith('depop_specifics.worldwideShipping.') ? 'Shipping'
            : 'Info';
  return fields.map(field => {
    const hint = field.path.startsWith('depop_specifics.attributes.') ? `Select up to ${field.max_values}` : hints[field.path];
    return { ...field, section: section(field.path),
      ...(['depop_specifics.nativeSizeId', 'quantity'].includes(field.path) ? { width: 'quarter' }
        : field.path === 'depop_specifics.sku' ? { width: 'half' } : {}),
      ...(hint ? { help: [typeof hint === 'function' ? hint(field.max_values) : hint, field.help].filter(Boolean).join(' ') } : {}) };
  });
}

function mercariPackageRows(listing) {
  const own = mercariOwnPackage(listing);
  const sides = String(listing.package_dimensions_in || '').split(/\s*[x×*]\s*/i).map(Number);
  const known = sides.length === 3 && sides.every(side => Number.isFinite(side) && side > 0);
  const pick = (key, fallback) => (own[key] ?? '') !== '' ? own[key] : fallback ?? '';
  const number = { input_type: 'number', min_value: 0, width: 'half' };
  return [
    nativeEditorField('mercari_specifics.package.pounds', 'Weight (lb)', pick('pounds', listing.weight_lb), { ...number, step: 1,
      help: 'How heavy will the package be? Think about the packaging you\u2019ll use. It can push your item into a higher weight class.' }),
    nativeEditorField('mercari_specifics.package.ounces', 'Weight (oz)', pick('ounces', listing.weight_oz), { ...number, max_value: 15.99 }),
    nativeEditorChoice('mercari_specifics.package.fitsShoebox', 'Will your item fit in a shoebox?', own.fitsShoebox || '',
      [{ label: 'Yes', value: 'yes' }, { label: 'No', value: 'no' }], { help: 'A standard shoebox measures 14\u201d x 10\u201d x 5\u201d.' }),
    nativeEditorField('mercari_specifics.package.length', 'Length (in)', pick('length', known ? sides[0] : ''), { ...number,
      help: 'How big will the package be? Get access to cheaper rates and avoid penalty fees when you input your package dimensions.' }),
    nativeEditorField('mercari_specifics.package.width', 'Width (in)', pick('width', known ? sides[1] : ''), number),
    nativeEditorField('mercari_specifics.package.height', 'Height (in)', pick('height', known ? sides[2] : ''), number),
  ];
}

function mercariEditorForm(listing, master) {
  const allCategories = mercariCatalogCategories(master);
  // The sell page offers every category with no subcategories, and below the top
  // level a category's "All ..." row picks the category itself.
  const categories = allCategories.filter(row => !row.parent || row.label.includes(' > '));
  if (master.itemCategories.some(row => Number(row.id) === mercariCategoryId(listing))) {
    listing = mercariCatalogListing(listing, master);
  } else listing = { ...listing, form: { ...listing.form, sizes: [] } };
  const category = mercariCategoryId(listing);
  const size = mercariSize(listing, category);
  const specifics = listing.mercari_specifics || {};
  const ownShipping = specifics.shippingMethod === 'SOYO';
  const sizes = listing.form?.sizes || [];
  const sizeChoices = sizes.map(row => ({ value: row.id, label: sizes.filter(other => other.name === row.name).length > 1
    ? `${row.name} · ${row.group_name || row.id}` : row.name || row.title }));
  const brand = mercariBrandRequest(listing);
  // Mercari's sell page, top to bottom (read 2026-10-27). Every field spans the row.
  const [title, description, price] = nativeEditorBase(listing, { marketplace: 'mercari', maxTitle: 80, maxDescription: 1000, minPrice: 1, maxPrice: 2000, priceStep: 1 });
  const product = section => row => ({ ...row, section });
  const fields = [
    title,
    description,
    nativeEditorChoice('direct_category_ids.mercari', 'Category', category || '', categories,
      { required: true, clears: ['mercari_specifics.nativeSizeId'] }),
    nativeEditorChoice('mercari_specifics.brand', 'Brand', brand.omit
      ? MERCARI_NO_BRAND
      : (master.itemBrands.find(row => marketplaceNameKey(row.name) === marketplaceNameKey(brand.name))?.name || brand.name || ''),
      [...master.itemBrands.map(row => ({ label: row.name, value: row.name })), { label: 'No brand / Not sure', value: MERCARI_NO_BRAND }],
      { clears: ['mercari_specifics.noBrand'] }),
    nativeEditorChoice('mercari_specifics.condition', 'Condition', mercariConditionId(specifics.condition || listing.condition),
      MERCARI_CONDITION_CHOICES.map(([label], index) => ({ label, value: index + 1 })),
      { required: true, help: MERCARI_CONDITION_CHOICES.map(([label, hint]) => `${label}: ${hint}`).join(' ') }),
  ].map(product('Product info'));
  const heldCategory = listing.form?.overrides?.categoryV2 || listing.marketplace_category_objects?.mercari;
  if (!categories.some(row => String(row.value) === String(category)) && String(heldCategory?.id) === String(category)) {
    const label = (heldCategory.displayPath || []).join(' > ');
    if (label) fields.find(row => row.path === 'direct_category_ids.mercari').option_values[label] = String(category);
  }
  // The size list sits under its group headings (Standard, Petite, Inch, Alpha); a group with no title has none.
  const sizeGroups = Object.fromEntries(sizeChoices.map((row, index) => [row.label, sizes[index].group_name])
    .filter(([, group]) => group));
  if (sizes.length || size.none === 'unknown') fields.push(product('Product info')(nativeEditorChoice('mercari_specifics.nativeSizeId', 'Size',
    size.id ?? specifics.nativeSizeId ?? specifics.size ?? listing.size ?? '', sizeChoices,
    { required: true, category_specific: true, ...(Object.keys(sizeGroups).length ? { option_groups: sizeGroups } : {}) })));
  fields.push(...[
    nativeEditorField('mercari_specifics.shipsFrom', 'Ships from', mercariShipsFrom(listing), { required: true, max_length: 5,
      help: 'The ZIP code this item ships from.' }),
    nativeEditorChoice('mercari_specifics.shippingMethod', 'Shipping method', specifics.shippingMethod ?? 'MERCARI',
      [{ label: 'Prepaid label', value: 'MERCARI' }, { label: 'Ship on your own', value: 'SOYO' }], { required: true,
        help: 'Prepaid label: we\'ll email you a label and you\'ll ship the item. Includes delivery protection. Ship on your own: you provide your own label and ship the item. It\'s not covered by shipping protection.' }),
    // Prepaid label's picker, read 2026-10-27: the package weight, whether it fits in a shoebox,
    // and the sides when it does not. They start from Item Details and set the labels offered.
    ...mercariPackageRows(listing),
    nativeEditorChoice('mercari_specifics.shippingClassId', 'Shipping label', listing.form?.specifics?.shipping?.carrierId || '',
      (listing.form?.shipping_classes || []).map(row => ({ label: `${row.carrierDisplayName} | ${row.name} | $${(row.fee / 100).toFixed(2)}`, value: row.id })),
      { required: true }),
    nativeEditorChoice('mercari_specifics.shippingPayerId', 'Offer buyers free shipping?', ownShipping ? 2 : specifics.shippingPayerId ?? 1,
      [{ label: 'Yes (Recommended)', value: 2 }, { label: 'No', value: 1 }]),
  ].map(product('Shipping')));
  fields.push(product('Pricing')({ ...price, label: 'Item price' }), ...[
    nativeEditorField('mercari_specifics.smartPricing.enabled', 'Adjusts price automatically', specifics.smartPricing?.enabled === true,
      { input_type: 'boolean', help: 'It lowers your price over time, boosts search visibility with each price change.' }),
    nativeEditorField('mercari_specifics.smartPricing.minimumPrice', 'Minimum price', specifics.smartPricing?.minimumPrice ?? '',
      { input_type: 'number', required: specifics.smartPricing?.enabled === true,
        help: 'The price will gradually move closer to your minimum price. Selling fee and shipping fee will be deducted from this price when the item is sold.' }),
  ].map(product('Smart pricing')));
  return { marketplace: 'mercari', native: true, known: true, category_id: String(category || ''),
    fields };
}

// Mercari's conditions and the line under each, as its sell page shows them.
const MERCARI_CONDITION_CHOICES = [
  ['New', 'New with tags (NWT). Unopened packaging. Unused.'],
  ['Like new', 'New without tags (NWOT). No signs of wear. Unused.'],
  ['Good', 'Gently used. One / few minor flaws. Functional.'],
  ['Fair', 'Used, functional, multiple flaws / defects.'],
  ['Poor', 'Major flaws, may be damaged, for parts.'],
];

async function handleNativeMarketplaceForm(msg) {
  const { marketplace, listing = {} } = msg.payload || {};
  const deadline = Date.now() + 20000;
  let answered = false;
  const reply = result => {
    if (answered) return;
    answered = true;
    sendBrowserResult(msg, result);
  };
  // A catalog read that never settles used to leave the form on "Loading…"
  // until Anteroom gave up and reported that Chrome had not answered.
  const timer = setTimeout(() => reply({ ok: false, error: 'The marketplace catalog did not answer in time. Refresh choices.' }), 20000);
  try {
    if (marketplace === 'poshmark') {
      const [catalog, display, tags, discounts] = await Promise.all([poshmarkCatalogData(deadline), poshmarkCatalogDisplay(deadline),
        poshmarkStyleTags(deadline), poshmarkShippingDiscounts(deadline)]);
      reply({ ok: true, form: poshmarkEditorForm(listing, catalog, display, tags, discounts) });
    } else if (marketplace === 'depop') {
      const attributes = await depopAttributes(deadline);
      let parcels = [];
      try {
        const providers = await depopAccountRead(`/api/v1/shipping-providers/?ids=USPS&itemCurrency=USD&itemCost=${encodeURIComponent(String(listing.price || 1))}`, 'parcel sizes', deadline);
        parcels = (Array.isArray(providers) ? providers : providers?.objects || []).flatMap(row => row.parcelSizes || row.parcel_sizes || []);
      } catch { /* Catalog fields remain usable without an account shipping read. */ }
      reply({ ok: true, form: depopEditorForm(listing, attributes, parcels) });
    } else if (marketplace === 'mercari') {
      const master = await mercariCatalogData(deadline);
      const prepared = mercariShippingListing(listing, await mercariShippingClasses(listing, deadline));
      reply({ ok: true, form: mercariEditorForm(prepared, master) });
    }
    else if (marketplace === 'sellwild') {
      // Sellwild's form is built in Anteroom; only the Shipping From address needs the seller's session.
      const addresses = await sellwildFetch('/api/addresses', { deadline, step: 'addresses' });
      reply({ ok: true, ship_from: addresses?.success ? sellwildShipFromText(addresses.addresses) : '' });
    }
    else reply({ ok: false, error: 'This marketplace has no Chrome form reader.' });
  } catch (error) {
    reply({ ok: false, error: error.message || 'The marketplace catalog could not be read.' });
  } finally {
    clearTimeout(timer);
  }
}
