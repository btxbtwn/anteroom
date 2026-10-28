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

const POSHMARK_SIZE_SYSTEMS = ['us', 'au', 'eu', 'uk'];

// Poshmark's seller fee, read from its earnings API on 2026-10-27: $2.95 under
// $15, 20% from $15 up, then the seller's shipping discount, and never below $0.
function poshmarkEarnings(price, discount = 0) {
  const amount = Number(price);
  if (!Number.isFinite(amount) || amount <= 0) return '';
  const cents = Math.round(amount * 100);
  const fee = amount < 15 ? 295 : Math.round(amount * 20);
  const off = Math.round(Number(discount) * 100) || 0;
  return `$${(Math.max(0, cents - fee - off) / 100).toFixed(2)}`;
}

function poshmarkSizeSystem(listing) {
  const system = String(listing?.poshmark_specifics?.sizeSystem || 'us').toLowerCase();
  return POSHMARK_SIZE_SYSTEMS.includes(system) ? system : 'us';
}

// Single Item until the seller chooses Multi Item, or an older listing already
// has more than one unit. The page's toggle is what the post's multi_item follows.
function poshmarkMultiDefault(listing) {
  const flag = listing?.poshmark_specifics?.multiItem;
  if (flag === true || flag === 'true') return 'true';
  if (flag === false || flag === 'false') return 'false';
  const rows = listing?.poshmark_specifics?.inventoryRows;
  if (Array.isArray(rows)) return rows.length > 1 || rows.some(row => Number(row?.quantity) > 1) ? 'true' : 'false';
  return Number(listing?.quantity) > 1 ? 'true' : 'false';
}

function poshmarkGroupSystems(set) {
  const systems = {};
  for (const key of POSHMARK_SIZE_SYSTEMS) {
    const sizes = set?.size_systems?.[key]?.sizes || [];
    if (!sizes.length) continue;
    systems[key] = sizes.map(size => ({
      id: size.id,
      label: size.display || size.id,
      equivalents: Object.fromEntries(Object.entries(size.equivalent_sizes || {}).flatMap(([sys, ids]) => {
        const id = Array.isArray(ids) ? ids.find(Boolean) : ids;
        return id ? [[sys, String(id)]] : [];
      })),
    }));
  }
  return systems;
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

// The same catalog as two dropdowns: departments, then categories (a group is
// one more step). The closed category reads "Women Jeans". Subcategory is that
// category's features, then None. A category with no features has no second list.
function poshmarkCategoryCascade(catalog, display) {
  const departments = new Map((catalog.catalog?.departments || []).map(row => [row.id, row]));
  const out = [];
  for (const node of display?.children || []) {
    const department = departments.get(node.id);
    if (!department) continue;
    const categories = new Map((department.categories || []).map(row => [row.id, row]));
    const itemFor = category => {
      const id = `${department.id}/${category.id}`;
      const features = category.category_features || [];
      return {
        type: 'category', label: category.display, value: id, display: `${department.display} ${category.display}`,
        children: features.length ? [...features.map(feature => ({ label: feature.display, value: `${id}/${feature.id}` })),
          { label: 'None', value: id }] : [],
      };
    };
    const items = [];
    for (const child of node.children || []) {
      if (child.type === 'group') {
        const nested = (child.children || []).flatMap(leaf => categories.has(leaf.id) ? [itemFor(categories.get(leaf.id))] : []);
        if (nested.length) items.push({ type: 'group', label: child.display, items: nested });
      } else if (categories.has(child.id)) items.push(itemFor(categories.get(child.id)));
    }
    if (items.length) out.push({ id: department.id, label: department.display, items });
  }
  return out;
}

const POSHMARK_CONDITION_CHOICES = [
  ['New With Tags (NWT)', 'nwt', 'Brand new with original tags or packaging'],
  ['Like New', 'uln', 'New without tags or like new'],
  ['Good', 'ug', 'Gently used with minimal signs of wear, to note in description or photos'],
  ['Fair', 'uf', 'Clearly used with visible wear, to note in description or photos'],
];

const POSHMARK_AVAILABILITY_HELP = [
  'For Sale: Your listing will be immediately available for purchase.',
  'Not For Sale: Your listing will be visible, but not available for purchase.',
  'Drops: Your listing will be visible, but not available for purchase until the designated drop time. The drop time can be scheduled up to 7 days in advance. Likers will be notified when your item drops.',
].join(' ');

// Each sell page's photo area (read 2026-10-28): its heading, empty where the page
// has none, its hint, and the caption under each photo slot. The photos, their
// order and the limit are the form's own pick in marketplace_photos.
const PAGE_PHOTOS = {
  depop: { section: 'Photos', help: 'Add up to 8 photos in JPEG or PNG format.',
    accept: 'image/jpeg,image/png,.jpg,.jpeg,.png',
    slots: ['Cover photo', 'Front', 'Back', 'Side', 'Label', 'Detail', 'Flaw'] },
  // Mercari's sell page has no Photos heading. The two lines sit at the top.
  mercari: { section: '', help: 'Add up to 12 photos\nOr drag and drop' },
  poshmark: { section: 'Photos & Video', help: 'Add up to 16 photos and 1 video to show the angles and details of your item.' },
};

function nativePhotosRow(marketplace) {
  const page = PAGE_PHOTOS[marketplace];
  return { ...nativeEditorField(`marketplace_photos.${marketplace}`, '', [], { input_type: 'photos', help: page.help }),
    section: page.section, ...(page.slots ? { slot_labels: page.slots } : {}), ...(page.accept ? { accept: page.accept } : {}) };
}

// Poshmark's create-listing page, read 2026-10-27: each heading with its hint, in
// page order. Quantity is its own Single Item / Multi Item control. Category and
// Subcategory sit side by side. Size carries the US/AU/EU/UK switch. Price is
// Listing Price with the Smart Sell badge, Discounted Shipping, and Your Earnings.
function poshmarkEditorForm(listing, catalog, display, styleTags = [], shippingDiscounts = []) {
  const resolved = poshmarkCategory(catalog, listing);
  const category = resolved?.category;
  const nativeSize = category ? poshmarkSize(category, listing, { kids: resolved.department.display === 'Kids', leaf: resolved.leaf }) : null;
  const sets = category?.size_sets || [];
  const specifics = listing.poshmark_specifics || {};
  const multi = poshmarkMultiDefault(listing) === 'true';
  const inventoryRows = Array.isArray(specifics.inventoryRows) ? specifics.inventoryRows : [{
    sizeSetId: nativeSize?.set?.id || (nativeSize?.size ? 'custom' : ''), sizeId: nativeSize?.size?.id || '',
    quantity: multi ? (listing.quantity ?? 1) : 1, sku: '',
  }];
  const at = (section, help) => row => ({ ...row, section, ...(help ? { help } : {}) });
  const half = row => ({ ...row, control_width: 'half' });
  const [title, description, price] = nativeEditorBase(listing, { marketplace: 'poshmark', maxTitle: 80, maxDescription: 1500, minPrice: 3, priceStep: 1 });
  const categoryChoices = poshmarkCategoryChoices(catalog, display);
  const discountRows = shippingDiscounts.map(row => ({ label: row.label, value: row.id, amount: Number(row.discount_amount?.val) || 0 }));
  const discountId = specifics.shippingDiscountId || discountRows.find(row => row.amount === 0)?.value || '';
  const discountAmount = discountRows.find(row => row.value === discountId)?.amount || 0;
  const fields = [
    nativePhotosRow('poshmark'),
    at('Title', 'Share key details like Brand, Size, and Color.')(title),
    at('Description', 'Tell shoppers about your listing by describing the Brand, Size, Color, Condition, Material, and any other noteworthy details.')(
      { ...description, input_type: 'textarea' }),
    at('Category', 'Add details to help your buyers find your item quickly.')(
      nativeEditorChoice('direct_category_ids.poshmark', 'Category', poshmarkDirectId(resolved), categoryChoices,
        { required: true, input_type: 'cascade', cascade: poshmarkCategoryCascade(catalog, display),
          clears: ['poshmark_specifics.subcategory', 'poshmark_specifics.sizeSetId', 'poshmark_specifics.nativeSizeId', 'poshmark_specifics.inventoryRows'] })),
    at('Quantity')(nativeEditorChoice('poshmark_specifics.multiItem', 'Quantity', poshmarkMultiDefault(listing),
      [{ label: 'Single Item', value: 'false' }, { label: 'Multi Item', value: 'true' }],
      { input_type: 'segmented', help: 'Select a single or multi item. If you have more than one of the same item for sale, select Multi Item to specify the quantity.' })),
    at('Size')(nativeEditorField('poshmark_specifics.inventoryRows', 'Size', inventoryRows,
      { input_type: 'inventory', required: true, category_specific: true, inventory_groups: [
        ...sets.map(set => {
          const systems = poshmarkGroupSystems(set);
          return { id: set.id, label: set.name || set.tags?.join(' / ') || set.id,
            sizes: (systems.us || []).map(size => ({ id: size.id, label: size.label })), systems };
        }),
        { id: 'custom', label: 'Custom', sizes: [] },
      ] })),
    at('Condition', ['Specify the condition of your item.', ...POSHMARK_CONDITION_CHOICES.map(([label, , hint]) => `${label}: ${hint}.`)].join(' '))(
      nativeEditorChoice('poshmark_specifics.condition', 'Condition', poshmarkCondition(listing),
        POSHMARK_CONDITION_CHOICES.map(([label, value]) => ({ label, value })), { required: true })),
    at('Brand', 'Optional. Add details to help your buyers find your item quickly.')(
      nativeEditorField('poshmark_specifics.brand', 'Brand',
        Object.hasOwn(specifics, 'brand') ? specifics.brand : (listing.brand ?? listing.form?.overrides?.brand), { max_length: 50 })),
    at('Color', 'Optional.')(
      nativeEditorChoice('poshmark_specifics.colors', 'Color', poshmarkColors(listing, catalog),
        (catalog.colors || []).map(color => color.name), { multi: true, max_values: 2 })),
    at('Style Tags', 'Optional. Make it easy for buyers to find your listings by selecting or creating up to 3 tags to describe your item (aesthetic, material, style, etc.)')(
      nativeEditorField('poshmark_specifics.styleTags', 'Style Tags', poshmarkPostStyleTags(listing, styleTags),
        { multi: true, max_values: 3, max_length: 25, options: styleTags, options_source: 'marketplace' })),
    ...[
      half({ ...price, label: 'Listing Price' }),
      nativeEditorField('poshmark_specifics.smartSell.enabled', 'Smart Sell',
        typeof specifics.smartSell?.enabled === 'boolean' ? specifics.smartSell.enabled : poshmarkSmartMinimum(listing, poshmarkPrice(listing)) > 0,
        { input_type: 'badge', help: 'Sell 50% faster by setting your lowest price.' }),
      nativeEditorField('poshmark_specifics.smartSell.minimumPrice', 'Lowest Price',
        specifics.smartSell?.minimumPrice ?? specifics.smartSellMinimumPrice ?? specifics.minimumPrice ?? listing.form?.specifics?.smartSell?.minPrice ?? '',
        { input_type: 'number', required: true, min_value: 0, step: 1, control_width: 'half', help: 'Sell 50% faster by setting your lowest price.' }),
      { ...nativeEditorChoice('poshmark_specifics.shippingDiscountId', 'Discounted Shipping', discountId,
        discountRows.map(row => ({ label: row.label, value: row.value })),
        { help: 'Optional', option_amounts: Object.fromEntries(discountRows.map(row => [row.value, row.amount])) }), control_width: 'half' },
      half(nativeEditorField('poshmark_specifics.earnings', 'Your Earnings', poshmarkEarnings(poshmarkPrice(listing), discountAmount),
        { locked: true, input_type: 'text' })),
    ].map(at('Price')),
    at('Availability', POSHMARK_AVAILABILITY_HELP)(nativeEditorChoice('poshmark_specifics.availability', 'Availability', specifics.availability || 'available',
      [{ label: 'For Sale', value: 'available' }, { label: 'Not For Sale', value: 'not_for_sale' }, { label: 'Drops', value: 'coming_soon' }],
      { required: true })),
    at('Availability', 'Your drop time must be within 7 days from today.')(
      nativeEditorField('poshmark_specifics.makeAvailableAt', 'Drop Time', specifics.makeAvailableAt || '',
        { input_type: 'datetime-local', required: true })),
    ...[
      half(nativeEditorField('poshmark_specifics.poshmarkSku', 'Listing SKU', specifics.poshmarkSku ?? listing.sku ?? '', { max_length: 50 })),
      half(nativeEditorField('cost', 'Cost Price', listing.cost, { input_type: 'number', min_value: 0, step: 0.01 })),
      nativeEditorField('poshmark_specifics.sellerNotes', 'Other Info', specifics.sellerNotes || '', { input_type: 'textarea', max_length: 500 }),
    ].map(at('Additional Details (Private)')),
  ];
  return { marketplace: 'poshmark', native: true, known: true, category_id: poshmarkDirectId(resolved), fields };
}

function depopEditorForm(listing, attributes, parcels = [], shipFrom = '') {
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
  // Depop's sell page, top to bottom. The page names its field labels in plain
  // English ("Body fit") and does not mark them with an asterisk.
  const fields = [
    nativePhotosRow('depop'),
    { ...nativeEditorBase(listing, { marketplace: 'depop', title: false, maxDescription: DEPOP_MAX_DESCRIPTION })[0],
      placeholder: 'eg. small grey Nike t-shirt, only worn a few times', counter: true, hashtag_max: DEPOP_MAX_HASHTAGS },
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
    nativeEditorField('depop.note.sku', 'Learn more about SKUs', '', { input_type: 'note' }),
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
  const sizes = depopParcelRows(parcels);
  const suggested = sizes.find(row => row.suggested);
  const parcelValue = specifics.parcelSize || specifics.parcel_size || suggested?.title || '';
  const parcelGroups = suggested ? Object.fromEntries(sizes.map(row => [row.title, row.suggested ? 'Suggested' : 'Change size'])) : {};
  fields.push({ ...price, label: 'Item price', prefix: 'US$' },
  nativeEditorField('depop.note.fees', 'No selling fees.', '', { input_type: 'note' }),
  nativeEditorField('depop.note.boost', 'Learn more about boosting', '', { input_type: 'note' }),
  // Off unless the seller turns it on: Depop charges an extra fee on the sale.
  nativeEditorField('depop_specifics.boost', 'Promote your item in search to help it sell faster. Pay an extra 12% fee, only if you sell.',
    specifics.boost === true, { input_type: 'boolean' }),
  nativeEditorField('depop.note.carrier', 'Depop Shipping (via USPS)', '', { input_type: 'note',
    help: 'You’ll receive a pre-paid shipping label and a QR code when you sell. Use either of these to ship at your nearest USPS Post Office.' }),
  nativeEditorField('depop_specifics.shipFrom', 'Shipping from', shipFrom, { input_type: 'textarea', locked: true,
    ...(shipFrom ? {} : { help: 'Sign in to Depop to show your shipping address.' }) }),
  nativeEditorChoice('depop_specifics.parcelSize', 'Package size', parcelValue,
    sizes.map(row => ({ label: row.title, value: row.id || row.title })),
    { required: true, ...(Object.keys(parcelGroups).length ? { option_groups: parcelGroups } : {}),
      ...(suggested ? { help: "Your selection sets your shipping label and the buyer's shipping cost. We've suggested a size based on your item — you can change it if needed." } : {}) }),
  nativeEditorField('depop_specifics.worldwideShipping.enabled', 'Offer worldwide shipping', specifics.worldwideShipping?.enabled === true,
    { input_type: 'boolean' }),
  // Always present so turning worldwide shipping on shows the fee without reloading the form.
  nativeEditorField('depop_specifics.worldwideShipping.price', 'Worldwide Shipping',
    specifics.worldwideShipping?.price ?? '', { input_type: 'number', required: true, min_value: 0, max_value: 99.99, step: 0.01, prefix: 'US$' }));
  return { marketplace: 'depop', native: true, known: true, page_labels: true, category_id: depopDirectId(category),
    fields: depopPageLayout(fields),
    notes: !sizes.length ? ['Sign in to Depop to load the seller’s parcel sizes.'] : [] };
}

// Depop's sell page, read 2026-10-27: its headings, the hint under each field,
// Size and Quantity at a quarter of the row each, SKU at half.
function depopPageLayout(fields) {
  const hints = {
    'depop_specifics.colour': 'Select up to two colors',
    'depop_specifics.source': max => `What kind of item is this? Add up to ${max}`,
    'depop_specifics.age': max => `Do you know when this item was made? Add up to ${max}`,
    'depop_specifics.style': max => `Pick relevant tags to describe this item's style. Add up to ${max}`,
  };
  const section = path => path.startsWith('marketplace_photos.') ? 'Photos'
    : path === 'depop_specifics.description' ? 'Description'
      : path === 'depop.note.sku' ? 'Info'
        : ['depop_specifics.source', 'depop_specifics.age', 'depop_specifics.style'].includes(path) ? 'Enhance your listing'
          : path === 'price' || path === 'depop.note.fees' ? 'Item price'
            : path === 'depop.note.boost' || path === 'depop_specifics.boost' ? 'Boost your listing'
              : path === 'depop.note.carrier' || path === 'depop_specifics.shipFrom' || path === 'depop_specifics.parcelSize'
                || path.startsWith('depop_specifics.worldwideShipping.') ? 'Shipping'
                : 'Info';
  return fields.map(field => {
    const hint = field.path.startsWith('depop_specifics.attributes.') ? `Select up to ${field.max_values}` : hints[field.path];
    return { ...field, section: section(field.path),
      ...(['depop_specifics.nativeSizeId', 'quantity'].includes(field.path) ? { width: 'quarter' }
        : field.path === 'depop_specifics.sku' ? { width: 'half' } : {}),
      ...(hint ? { help: [typeof hint === 'function' ? hint(field.max_values) : hint, field.help].filter(Boolean).join(' ') } : {}) };
  });
}

// Prepaid label's picker on Mercari, read 2026-10-28: one weight question with lb and oz
// side by side, the shoebox question, and only after No the three sides (each "in")
// or "Skip dimension input". Values start from Item Details.
function mercariPackageRows(listing) {
  const own = mercariOwnPackage(listing);
  const details = mercariDetailSides(listing) || [];
  const pick = (key, fallback) => (own[key] ?? '') !== '' ? own[key] : fallback ?? '';
  const weight = { input_type: 'number', min_value: 0, width: 'quarter', placeholder: '0',
    group: 'How heavy will the package be?',
    group_help: 'Think about the packaging you\u2019ll use. It can push your item into a higher weight class.' };
  const side = { input_type: 'number', min_value: 0, width: 'third', suffix: 'in', placeholder: '0',
    group: 'How big will the package be?',
    group_help: 'Get access to cheaper rates and avoid penalty fees when you input your package dimensions.' };
  return [
    nativeEditorField('mercari_specifics.package.pounds', 'lb', pick('pounds', listing.weight_lb), { ...weight, step: 1 }),
    nativeEditorField('mercari_specifics.package.ounces', 'oz', pick('ounces', listing.weight_oz), { ...weight, max_value: 15.99 }),
    nativeEditorChoice('mercari_specifics.package.fitsShoebox', 'Will your item fit in a shoebox?', mercariShoeboxAnswer(listing),
      [{ label: 'Yes', value: 'yes' }, { label: 'No', value: 'no' }],
      { required: true, presentation: 'radio', help: 'A standard shoebox measures 14\u201d x 10\u201d x 5\u201d.' }),
    nativeEditorField('mercari_specifics.package.length', 'Length', pick('length', details[0]), side),
    nativeEditorField('mercari_specifics.package.width', 'Width', pick('width', details[1]), side),
    nativeEditorField('mercari_specifics.package.height', 'Height', pick('height', details[2]), side),
    nativeEditorField('mercari_specifics.package.skipDimensions', 'Skip dimension input', own.skipDimensions === true || own.skipDimensions === 'true',
      { input_type: 'boolean', presentation: 'button', group: side.group, group_help: side.group_help }),
  ];
}

// A label as Mercari's list shows it: the price, the price before discount when higher, and the days.
function mercariLabelLine(row) {
  const dollars = cents => `$${(Number(cents) / 100).toFixed(2)}`;
  const before = Math.max(Number(row.nonPromotionalFee) || 0, Number(row.standardFee) || 0);
  return [`${dollars(row.fee)}${before > Number(row.fee) ? ` ${dollars(before)}` : ''}`, row.etaForSeller].filter(Boolean).join(' | ');
}

// The line under the chosen label: "Up to 2 lb | 2 - 8 days | Buyer pays $8.49".
function mercariLabelSummary(row, sellerPays) {
  if (!row) return '';
  const ounces = Number(row.maxWeight) || 0;
  const weight = ounces < 16 ? `Up to ${ounces} oz` : `Up to ${Math.round(ounces / 16 * 100) / 100} lb`;
  const detail = [weight, row.etaForSeller, `${sellerPays ? 'You pay' : 'Buyer pays'} $${(Number(row.fee) / 100).toFixed(2)}`].filter(Boolean).join(' | ');
  return detail ? `Recommended: ${detail}` : '';
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
  // Mercari's sell page, top to bottom (read 2026-10-27, rechecked 2026-10-28).
  const [title, description, price] = nativeEditorBase(listing, { marketplace: 'mercari', maxTitle: 80, maxDescription: 1000, minPrice: 1, maxPrice: 2000, priceStep: 1 });
  const product = section => row => ({ ...row, section });
  const classes = listing.form?.shipping_classes || [];
  const labelId = listing.form?.specifics?.shipping?.carrierId;
  const payer = ownShipping ? 2 : specifics.shippingPayerId ?? 1;
  const fields = [
    { ...title, placeholder: 'What are you selling?', counter: true },
    { ...description, placeholder: 'Describe your item (5+ words)', counter: true },
    nativeEditorChoice('direct_category_ids.mercari', 'Category', category || '', categories,
      { required: true, clears: ['mercari_specifics.nativeSizeId'] }),
    nativeEditorChoice('mercari_specifics.brand', 'Brand', brand.omit
      ? MERCARI_NO_BRAND
      : (master.itemBrands.find(row => marketplaceNameKey(row.name) === marketplaceNameKey(brand.name))?.name || brand.name || ''),
      [...master.itemBrands.map(row => ({ label: row.name, value: row.name })), { label: 'No brand / Not sure', value: MERCARI_NO_BRAND }],
      { clears: ['mercari_specifics.noBrand'], placeholder: 'Select brand' }),
    nativeEditorChoice('mercari_specifics.condition', 'Condition', mercariConditionId(specifics.condition || listing.condition),
      MERCARI_CONDITION_CHOICES.map(([label], index) => ({ label, value: index + 1 })),
      { required: true, presentation: 'radio-row', option_help: Object.fromEntries(MERCARI_CONDITION_CHOICES) }),
  ].map(product('Product info'));
  fields.unshift(nativePhotosRow('mercari'));
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
    { required: true, category_specific: true, placeholder: 'Select size', ...(Object.keys(sizeGroups).length ? { option_groups: sizeGroups } : {}) })));
  fields.push(...[
    nativeEditorField('mercari_specifics.shipsFrom', 'Ships from:', mercariShipsFrom(listing),
      { required: true, max_length: 5, width: 'quarter', save_as_default: true }),
    nativeEditorChoice('mercari_specifics.shippingMethod', 'Shipping method', specifics.shippingMethod ?? 'MERCARI',
      [{ label: 'Prepaid label', value: 'MERCARI' }, { label: 'Ship on your own', value: 'SOYO' }], { required: true, presentation: 'radio',
        option_help: {
          'Prepaid label': 'We\'ll email you a label and you\'ll ship the item. Includes delivery protection. Save an average of 25% off retail.',
          'Ship on your own': 'You provide your own label and ship the item. It\u2019s not covered by shipping protection.',
        } }),
    ...mercariPackageRows(listing),
    nativeEditorChoice('mercari_specifics.shippingClassId', 'Shipping label', labelId || '',
      classes.map(row => ({ label: row.carrierDisplayName || row.name, value: row.id })),
      { required: true, width: 'half', save_as_default: true,
        option_help: Object.fromEntries(classes.map(row => [row.carrierDisplayName || row.name, mercariLabelLine(row)])),
        help: mercariLabelSummary(classes.find(row => String(row.id) === String(labelId)), Number(payer) === 2) }),
    nativeEditorChoice('mercari_specifics.shippingPayerId', 'Offer buyers free shipping?', payer,
      [{ label: 'Yes (Recommended)', value: 2 }, { label: 'No', value: 1 }], { width: 'half' }),
  ].map(product('Shipping')));
  const dollars = Number(listing.price || 0);
  const recommended = dollars >= 1 ? Math.round(dollars * 0.85) : 0;
  // Mercari turns Smart pricing on once a price is entered, and fills the floor with 85% of it.
  const savedSmart = specifics.smartPricing;
  const smartOn = savedSmart ? savedSmart.enabled === true : dollars >= 1;
  const minimumPrice = savedSmart && savedSmart.minimumPrice !== undefined && savedSmart.minimumPrice !== ''
    ? savedSmart.minimumPrice
    : (smartOn && recommended > 0 ? recommended : '');
  fields.push(product('Pricing')({ ...price, label: 'Item price', label_hidden: true, prefix: '$', placeholder: '(min $1/max $2000)' }), ...[
    nativeEditorField('mercari_specifics.smartPricing.enabled', 'Adjusts price automatically', smartOn,
      { input_type: 'boolean', section_help: 'Increase your chances of a sale by gradually lowering your price.',
        help: 'It lowers your price over time, boosts search visibility with each price change.' }),
    nativeEditorField('mercari_specifics.smartPricing.minimumPrice', 'Minimum price', minimumPrice,
      { input_type: 'number', required: smartOn, prefix: '$', placeholder: '0.00', width: 'quarter',
        help: [recommended > 0 ? `Recommended: $${recommended}.` : '', 'The price will gradually move closer to your minimum price. Selling fee and shipping fee will be deducted from this price when the item is sold.'].filter(Boolean).join(' ') }),
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
      let shipFrom = '';
      try {
        const params = depopShippingParams(depopCategory(listing, attributes));
        const price = Number(listing.price);
        if (Number.isFinite(price) && price > 0) {
          params.set('item_cost', price.toFixed(2));
          params.set('item_currency', 'USD');
        }
        parcels = depopParcelRows(await depopAccountRead(`/presentation/api/v1/shipping/providers/depop/?${params}`, 'parcel sizes', deadline));
      } catch { /* Catalog fields remain usable without an account shipping read. */ }
      try {
        shipFrom = depopShipFromLine(depopAddress(listing, await depopAccountRead('/api/v1/addresses/?providers=USPS', 'ship-from addresses', deadline)));
      } catch { /* The address card stays empty until Depop answers. */ }
      reply({ ok: true, form: depopEditorForm(listing, attributes, parcels, shipFrom) });
    } else if (marketplace === 'mercari') {
      const master = await mercariCatalogData(deadline);
      const prepared = mercariShippingListing(listing, await mercariShippingClasses(listing, deadline));
      reply({ ok: true, form: mercariEditorForm(prepared, master) });
    }
    else if (marketplace === 'sellwild') {
      // Sellwild's form is built in Anteroom. The session supplies the address card,
      // the seller's coupons, and a profile package when one is set.
      const [addresses, profile, coupons] = await Promise.all([
        sellwildFetch('/api/addresses', { deadline, step: 'addresses' }),
        sellwildFetch('/api/user/profile', { deadline, step: 'profile' }).catch(() => null),
        sellwildFetch('/api/seller/coupons?limit=50', { deadline, step: 'coupons' }).catch(() => null),
      ]);
      const rows = Array.isArray(coupons?.coupons) ? coupons.coupons : Array.isArray(coupons?.data) ? coupons.data : Array.isArray(coupons) ? coupons : [];
      reply({ ok: true, ship_from: addresses?.success ? sellwildShipFromText(addresses.addresses) : '',
        default_parcel: String(profile?.profile?.defaultParcelTemplate || profile?.defaultParcelTemplate || ''),
        coupons: rows.filter(row => row?.id != null).map(row => ({ id: row.id, name: String(row.name || row.code || ''),
          discountType: row.discountType || row.discount_type || '', discountValue: row.discountValue ?? row.discount_value })) });
    }
    else reply({ ok: false, error: 'This marketplace has no Chrome form reader.' });
  } catch (error) {
    reply({ ok: false, error: error.message || 'The marketplace catalog could not be read.' });
  } finally {
    clearTimeout(timer);
  }
}
