const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, call } = require('./worker-context');

const clean = value => JSON.parse(JSON.stringify(value));
const MERCARI = { itemCategories: [{ id: 1, name: 'Tees', parentId: 0, itemSizeGroupId: 1 },
  { id: 3, name: 'Kids', parentId: 0, itemSizeGroupId: 0 },
  { id: 1877, name: 'Girls tops & t-shirts', parentId: 3, itemSizeGroupId: 0 },
  { id: 1911, name: 'Girls (4+)', parentId: 1877, itemSizeGroupId: 1 }],
  itemSizeGroups: [{ id: 1, sizeIds: [1, 2], subGroupIds: [] }], itemSizeSubGroups: [],
  itemSizes: [{ id: 1, title: 'XL' }, { id: 2, title: '2XL' }], itemBrands: [{ id: 30138, name: 'GB Girls' }] };
const sizeSet = (id, tags, sizes) => ({ id, name: tags.join(' / '), tags,
  size_systems: { us: { sizes: sizes.map(id => ({ id, display: id })) } } });
const POSH = { colors: [{ name: 'Black' }], catalog: { departments: [{ id: 'men', display: 'Men', categories: [
  { id: 'shirts', display: 'Shirts', category_features: [{ id: 'tees', display: 'Tees' }],
    size_sets: [sizeSet('standard', ['standard'], ['M', 'XL']), sizeSet('tall', ['big_&_tall'], ['XLT'])] },
  { id: 'shoes', display: 'Shoes', size_sets: [sizeSet('shoes', ['standard'], ['10', '11'])] },
] }] } };
const POSH_DISPLAY = { type: 'root', children: [{ type: 'department', id: 'men', children: [
  { type: 'category', id: 'shirts' }, { type: 'category', id: 'shoes' }] }] };
const POSH_LISTING = { title: 'A tee', description: 'Details', price: 16, size: 'M', condition: 'Good',
  direct_category_ids: { poshmark: 'men/shirts/tees' }, form: { aspects: { tees_size: 'M', tees_size_scale: 'standard' },
    specifics: { smartSell: { enabled: true, minPrice: 10 } } }, tags: ['Casual'] };
const DEPOP = { department: [{ id: 'menswear', name: 'Men' }], group: [{ id: 'bottoms', name: 'Bottoms', department: ['menswear'],
  product_types: [{ id: 'jeans', name: 'Jeans', attribute_ids: ['bottom-fit'] }] }],
  category_size_mapping: [{ department: 'menswear', product_type: 'jeans', size_set_by_region: { US: 12 } }],
  size_sets: [{ id: 12, sizes: [{ id: 1, name: '30' }, { id: 2, name: '32' }] }], brand: [{ id: 'other', name: 'Other' }],
  generic_attributes: [{ id: 'bottom-fit', name: 'Fit', is_mandatory: true, max_selection: 2,
    attributes: [{ id: 'straight', name: 'Straight' }, { id: 'retired', name: 'Retired', status: 'inactive' }] }],
  age: { max_selection: 1, attributes: [{ id: 'y2k', name: '00s' }] },
  source: { max_selection: 2, attributes: [{ id: 'preloved', name: 'Preloved' }] },
  style: { max_selection: 3, attributes: [{ id: 'casual', name: 'Casual' }] } };

test('Poshmark title, description and brand follow General until this form sets them', () => {
  const c = loadWorker();
  const form = call(c, 'poshmarkEditorForm', { ...POSH_LISTING, brand: 'Nike' }, POSH, POSH_DISPLAY);
  const fields = Object.fromEntries(form.fields.map(row => [row.path, row]));
  assert.equal(fields['poshmark_specifics.title'].value, 'A tee');
  assert.equal(fields['poshmark_specifics.description'].value, 'Details');
  assert.equal(fields['poshmark_specifics.brand'].value, 'Nike');
  assert.equal(fields.price.path, 'price');
  const overridden = call(c, 'poshmarkEditorForm', {
    ...POSH_LISTING, brand: 'Nike',
    poshmark_specifics: { title: 'Posh title', description: '', brand: 'Posh brand' },
  }, POSH, POSH_DISPLAY);
  const rows = Object.fromEntries(overridden.fields.map(row => [row.path, row]));
  assert.equal(rows['poshmark_specifics.title'].value, 'Posh title');
  assert.equal(rows['poshmark_specifics.description'].value, '');
  assert.equal(rows['poshmark_specifics.brand'].value, 'Posh brand');
});

test('Poshmark form uses native category, group and size IDs and only supported fields', () => {
  const c = loadWorker();
  const form = clean(call(c, 'poshmarkEditorForm', POSH_LISTING, POSH, POSH_DISPLAY, ['Casual'], [{ id: 'discount-1', label: 'Buyer Shipping: Free' }]));
  const fields = Object.fromEntries(form.fields.map(row => [row.path, row]));
  assert.equal(form.native, true);
  assert.deepEqual(fields['poshmark_specifics.shippingDiscountId'].option_values, { 'Buyer Shipping: Free': 'discount-1' });
  assert.deepEqual(fields['poshmark_specifics.availability'].option_values, { 'For Sale': 'available', 'Not For Sale': 'not_for_sale', Drops: 'coming_soon' });
  assert.equal(fields['poshmark_specifics.sellerNotes'].value, '');
  assert.equal(fields['direct_category_ids.poshmark'].value, 'men/shirts/tees');
  assert.equal(fields['direct_category_ids.poshmark'].option_values['Men > Shirts > Tees'], 'men/shirts/tees');
  assert.equal(fields['direct_category_ids.poshmark'].option_values['Men > Shirts > None'], 'men/shirts');
  assert.equal(fields['direct_category_ids.poshmark'].option_values['Men > Shoes'], 'men/shoes');
  assert.equal(fields['poshmark_specifics.subcategory'], undefined);
  const kept = call(c, 'poshmarkCategory', POSH, { ...POSH_LISTING, poshmark_specifics: { subcategory: 'None' } });
  assert.equal(kept.leaf, 'tees');
  const parent = call(c, 'poshmarkCategory', POSH, { ...POSH_LISTING, direct_category_ids: { poshmark: 'men/shirts' }, poshmark_specifics: { subcategory: 'None' } });
  assert.equal(parent.leaf, 'shirts');
  const narrowed = call(c, 'poshmarkCategory', POSH, { ...POSH_LISTING, direct_category_ids: { poshmark: 'men/shirts' }, poshmark_specifics: { subcategory: 'Tees' } });
  assert.equal(narrowed.leaf, 'tees');
  const inventory = fields['poshmark_specifics.inventoryRows'];
  assert.equal(inventory.input_type, 'inventory');
  assert.deepEqual(inventory.value, [{ sizeSetId: 'standard', sizeId: 'M', quantity: 1, sku: '' }]);
  assert.deepEqual(inventory.inventory_groups.map(row => row.id), ['standard', 'tall', 'custom']);
  assert.deepEqual(inventory.inventory_groups[0].sizes.map(row => row.id), ['M', 'XL']);
  assert.equal(fields['poshmark_specifics.smartSell.enabled'].input_type, 'badge');
  assert.ok(!fields.cost.locked);
  assert.ok(!form.fields.some(row => /shipping label|size scale/i.test(row.label)));
});

// Read from Poshmark's create-listing page on 2026-10-28.
test('Poshmark form follows the create-listing page: headings, order, grouped categories and one Color field', () => {
  const c = loadWorker();
  const catalog = { ...POSH, colors: [{ name: 'Red' }, { name: 'Blue' }, { name: 'Black' }], catalog: { departments: [
    ...POSH.catalog.departments,
    { id: 'women', display: 'Women', categories: [{ id: 'kilts', display: 'Kilts', size_sets: [] }, { id: 'tops', display: 'Tops', size_sets: [] }] }] } };
  // The page orders Women first, puts Tops before the group, and Kilts inside Global & Traditional Wear.
  const display = { children: [
    { type: 'department', id: 'women', children: [{ type: 'category', id: 'tops' },
      { type: 'group', display: 'Global & Traditional Wear', children: [{ type: 'category', id: 'kilts' }] }] },
    ...POSH_DISPLAY.children] };
  const listing = { ...POSH_LISTING, primaryColor: 'Blue', secondaryColor: 'Black', cost: 4 };
  const form = clean(call(c, 'poshmarkEditorForm', listing, catalog, display, [], [{ id: 'free', label: 'Free', discount_amount: { val: '6.49' } }]));
  const rows = Object.fromEntries(form.fields.map(row => [row.label, row]));
  assert.deepEqual(rows.Category.options, ['Women > Tops', 'Women > Global & Traditional Wear > Kilts',
    'Men > Shirts > Tees', 'Men > Shirts > None', 'Men > Shoes']);
  const headings = [];
  for (const row of form.fields) if (headings.at(-1) !== row.section) headings.push(row.section);
  assert.deepEqual(headings, ['Photos & Video', 'Title', 'Description', 'Category', 'Quantity', 'Size', 'Condition', 'Brand', 'Color', 'Style Tags', 'Price',
    'Availability', 'Additional Details (Private)']);
  assert.deepEqual([form.fields[0].path, form.fields[0].input_type, form.fields[0].help],
    ['marketplace_photos.poshmark', 'photos', 'Add up to 16 photos and 1 video to show the angles and details of your item.']);
  assert.deepEqual(form.fields.filter(row => row.section === 'Price').map(row => row.label),
    ['Listing Price', 'Smart Sell', 'Lowest Price', 'Discounted Shipping', 'Your Earnings']);
  assert.deepEqual(form.fields.filter(row => row.section === 'Additional Details (Private)').map(row => row.label),
    ['Listing SKU', 'Cost Price', 'Other Info']);
  assert.deepEqual(rows.Color.options, ['Red', 'Blue', 'Black']);
  assert.deepEqual(rows.Color.value, ['Blue', 'Black']);
  assert.equal(rows.Color.max_values, 2);
  assert.equal(rows['Cost Price'].value, 4);
  assert.ok(!rows['Cost Price'].locked);
  assert.equal(rows['Smart Sell'].help, 'Sell 50% faster by setting your lowest price.');
  assert.equal(rows['Your Earnings'].locked, true);
  assert.equal(rows['Your Earnings'].value, '$12.80');
  assert.equal(rows.Size.inventory_groups.at(-1).label, 'Custom');
  // The form's Color picks are what direct List posts.
  assert.deepEqual(clean(call(c, 'poshmarkColors', { ...listing, poshmark_specifics: { colors: ['Red'] } }, catalog)), ['Red']);
  assert.deepEqual(clean(call(c, 'poshmarkColors', listing, catalog)), ['Blue', 'Black']);
  const jeans = form.fields.find(row => row.path === 'direct_category_ids.poshmark').cascade;
  assert.equal(jeans[0].label, 'Women');
  assert.equal(jeans[0].items[0].display, 'Women Tops');
  assert.equal(jeans[0].items[1].type, 'group');
  assert.equal(jeans[0].items[1].items[0].display, 'Women Kilts');
  assert.equal(form.fields.find(row => row.path === 'poshmark_specifics.multiItem').input_type, 'segmented');
  assert.equal(form.fields.find(row => row.path === 'poshmark_specifics.makeAvailableAt').label, 'Drop Time');
});

test('Poshmark sizes follow the selected system and earnings subtract the seller shipping discount', () => {
  const c = loadWorker();
  const catalog = { colors: [], catalog: { departments: [{ id: 'women', display: 'Women', categories: [{ id: 'jeans', display: 'Jeans',
    size_sets: [{ id: 'standard', name: 'Standard', tags: ['standard'], size_systems: {
      us: { sizes: [{ id: '23', display: '23', equivalent_sizes: { au: ['0AU'] } }] },
      au: { sizes: [{ id: '0AU', display: '0', equivalent_sizes: { us: ['23'] } }] },
    } }] }] }] } };
  const display = { children: [{ id: 'women', children: [{ id: 'jeans' }] }] };
  const form = clean(call(c, 'poshmarkEditorForm', { price: 20, direct_category_ids: { poshmark: 'women/jeans' } }, catalog, display, [],
    [{ id: 'free', label: 'Free', discount_amount: { val: '6.49' } }, { id: 'none', label: 'No Discount', discount_amount: { val: '0' } }]));
  const inventory = form.fields.find(row => row.input_type === 'inventory');
  assert.deepEqual(inventory.inventory_groups[0].sizes.map(row => row.id), ['23']);
  assert.deepEqual(inventory.inventory_groups[0].systems.au.map(row => row.label), ['0']);
  assert.equal(inventory.inventory_groups[0].systems.us[0].equivalents.au, '0AU');
  assert.equal(form.fields.find(row => row.label === 'Your Earnings').value, '$16.00');
  const discounted = clean(call(c, 'poshmarkEditorForm', { price: 20, poshmark_specifics: { shippingDiscountId: 'free' } }, catalog, display, [],
    [{ id: 'free', label: 'Free', discount_amount: { val: '6.49' } }]));
  assert.equal(discounted.fields.find(row => row.label === 'Your Earnings').value, '$9.51');
  assert.equal(call(c, 'poshmarkEarnings', 10, 0), '$7.05');
  assert.equal(call(c, 'poshmarkEarnings', 3, 6.49), '$0.00');
});

test('explicit Poshmark group and native size beat the old imported size, without crossing groups', () => {
  const c = loadWorker();
  const listing = { ...POSH_LISTING, poshmark_specifics: { sizeSetId: 'tall', nativeSizeId: 'XLT' } };
  const category = call(c, 'poshmarkCategory', POSH, listing);
  assert.equal(call(c, 'poshmarkSize', category.category, listing, { leaf: 'tees' }).size.id, 'XLT');
  listing.poshmark_specifics.nativeSizeId = 'XL';
  assert.ok(call(c, 'poshmarkSize', category.category, listing).problem);
  listing.poshmark_specifics = { sizeSetId: '', nativeSizeId: '' };
  assert.ok(call(c, 'poshmarkSize', category.category, listing).problem);
});

test('changing Poshmark category replaces the size list and refuses the previous group', () => {
  const c = loadWorker();
  const listing = { ...POSH_LISTING, direct_category_ids: { poshmark: 'men/shoes' }, poshmark_specifics: { sizeSetId: 'standard', nativeSizeId: 'XL' } };
  const form = call(c, 'poshmarkEditorForm', listing, POSH, POSH_DISPLAY);
  const inventory = form.fields.find(row => row.input_type === 'inventory');
  assert.equal(inventory.value[0].sizeId, '');
  assert.deepEqual(clean(inventory.inventory_groups[0].sizes.map(row => row.id)), ['10', '11']);
  assert.ok(call(c, 'poshmarkSize', POSH.catalog.departments[0].categories[1], listing).problem);
});

test('explicit Smart Pricing off and empty style tags survive older form defaults', () => {
  const c = loadWorker();
  const listing = { ...POSH_LISTING, poshmark_specifics: { smartSell: { enabled: false, minimumPrice: 8 }, styleTags: [] } };
  assert.equal(call(c, 'poshmarkSmartMinimum', listing, 16), 0);
  assert.deepEqual(clean(call(c, 'poshmarkPostStyleTags', listing, ['Casual'])), []);
  listing.poshmark_specifics.smartSell.enabled = true;
  assert.equal(call(c, 'poshmarkSmartMinimum', listing, 16), 8);
});

test('Mercari ship on your own records seller-paid shipping, and the form keeps only the page\'s shipping fields', () => {
  const c = loadWorker();
  const form = call(c, 'mercariEditorForm', { title: 'A tee', direct_category_ids: { mercari: 1 }, zipCode: '70125',
    mercari_specifics: { shippingMethod: 'SOYO', shippingPayerId: 1 } }, MERCARI);
  const fields = Object.fromEntries(form.fields.map(row => [row.path, row]));
  assert.equal(fields['mercari_specifics.shippingMethod'].value, 'SOYO');
  assert.equal(fields['mercari_specifics.shippingPayerId'].value, 2);
  assert.deepEqual(fields['mercari_specifics.shippingPayerId'].option_values, { 'Yes (Recommended)': '2', No: '1' });
  assert.equal(fields['mercari_specifics.shipsFrom'].value, '70125');
  assert.ok(fields['mercari_specifics.shippingClassId']);
  assert.ok(!fields.weight_oz && !fields.package_dimensions_in);
});

test('Depop worldwide shipping fee keeps its monetary limits even when the offer is off', () => {
  const c = loadWorker();
  const listing = { direct_category_ids: { depop: 'menswear_jeans' }, depop_specifics: { worldwideShipping: { enabled: true, price: 0 } } };
  const form = call(c, 'depopEditorForm', listing, DEPOP);
  const fee = form.fields.find(row => row.path === 'depop_specifics.worldwideShipping.price');
  assert.deepEqual([fee.value, fee.min_value, fee.max_value, fee.step], [0, 0, 99.99, 0.01]);
  listing.depop_specifics.worldwideShipping.enabled = false;
  const hidden = call(c, 'depopEditorForm', listing, DEPOP).fields.find(row => row.path === fee.path);
  assert.equal(hidden.input_type, 'number');
  assert.equal(hidden.max_value, 99.99);
});

test('Depop form reads native general attribute objects, maximums and category attributes', () => {
  const c = loadWorker();
  const listing = { direct_category_ids: { depop: 'menswear_jeans' }, size: '30', depop_specifics: { attributes: { 'bottom-fit': ['straight'] } } };
  const form = clean(call(c, 'depopEditorForm', listing, DEPOP, [{ title: 'Medium' }]));
  const fields = Object.fromEntries(form.fields.map(row => [row.path, row]));
  assert.deepEqual(fields['direct_category_ids.depop'].option_values, { 'Men > Bottoms > Jeans': 'menswear_jeans' });
  assert.deepEqual(fields['depop_specifics.age'].option_values, { '00s': 'y2k' });
  assert.equal(fields['depop_specifics.age'].max_values, 1);
  assert.equal(fields['depop_specifics.source'].max_values, 2);
  assert.deepEqual(fields['depop_specifics.attributes.bottom-fit'].options, ['Straight']);
  assert.ok(fields['depop_specifics.attributes.bottom-fit'].required);
  assert.equal(fields['depop_specifics.nativeSizeId'].value, 1);
  assert.equal(fields['depop_specifics.nativeSizeId'].category_specific, true);
  assert.equal(fields['depop_specifics.attributes.bottom-fit'].category_specific, true);
  assert.ok(!fields['depop_specifics.age'].category_specific);
  assert.ok(!form.fields.some(row => row.path === 'title'));
});

test('Depop native size, condition and attributes are the ones sent to its draft', () => {
  const c = loadWorker();
  const listing = { direct_category_ids: { depop: 'menswear_jeans' }, size: '30', condition: 'Good',
    form: { aspects: { menswear_jeans_size: '1' }, overrides: { condition: 'used_good' } },
    depop_specifics: { nativeSizeId: '2', condition: 'used_excellent', age: ['y2k'], attributes: { 'bottom-fit': ['straight'] } } };
  const category = call(c, 'depopCategory', listing, DEPOP);
  assert.equal(call(c, 'depopSize', listing, category, DEPOP).id, 2);
  assert.deepEqual(clean(call(c, 'depopAttributeValues', listing, category, DEPOP)), { values: { 'bottom-fit': ['straight'] }, missing: [] });
  assert.equal(call(c, 'depopFormFields', listing).condition, 'used_excellent');
  assert.deepEqual(clean(call(c, 'depopFormFields', listing).age), ['y2k']);
  listing.depop_specifics.nativeSizeId = '';
  assert.ok(call(c, 'depopSize', listing, category, DEPOP).problem);
  listing.depop_specifics.attributes['bottom-fit'] = [];
  assert.equal(call(c, 'depopAttributeValues', listing, category, DEPOP).missing[0].field, 'bottom-fit');
});

test('Depop maps generated labels and explicit editor arrays through the same native option definitions', () => {
  const c = loadWorker();
  const listing = { depop_specifics: { age: '00s', source: 'Preloved', style: ['Casual'] } };
  assert.deepEqual(clean(call(c, 'depopFormFields', listing, DEPOP)), { age: ['y2k'], source: ['preloved'], style: ['casual'] });
  listing.depop_specifics = { age: [], source: [], style: [] };
  listing.form = { specifics: { age: ['y2k'], source: ['preloved'], style: ['casual'] } };
  assert.deepEqual(clean(call(c, 'depopFormFields', listing, DEPOP)), { age: [], source: [], style: [] });
});

test('explicit Depop choices stay on their canonical fields and category changes preserve them', () => {
  const c = loadWorker();
  const listing = { direct_category_ids: { depop: 'menswear_jeans' }, depop_specifics: {
    age: [], source: [], style: [], attributes: { 'bottom-fit': ['straight'] },
  } };
  assert.deepEqual(clean(call(c, 'depopFormFields', listing, DEPOP)), { age: [], source: [], style: [] });
  const form = clean(call(c, 'depopEditorForm', listing, DEPOP));
  const category = form.fields.find(row => row.label === 'Category');
  assert.ok(category.clears.includes('depop_specifics.attributes'));
  for (const key of ['age', 'source', 'style']) {
    assert.ok(!category.clears.includes(`depop_specifics.${key}`));
    assert.deepEqual(form.fields.find(row => row.path === `depop_specifics.${key}`).value, []);
  }
});

test('Mercari shows the seller’s brand and offers a category with subcategories the way its picker does', () => {
  const c = loadWorker();
  const listing = { brand: 'GB girls', form: { overrides: { brand: '30138', categoryV2: {
    id: '1877', displayPath: ['Kids', 'Girls tops & t-shirts'], hasChildren: true,
  } } } };
  const form = clean(call(c, 'mercariEditorForm', listing, MERCARI));
  const brand = form.fields.find(row => row.label === 'Brand');
  assert.equal(brand.path, 'mercari_specifics.brand');
  assert.equal(brand.value, 'GB Girls');
  assert.equal(brand.option_values['No brand / Not sure'], 'No Brand/Not sure');
  assert.ok(!form.fields.some(row => ['mercari_specifics.sizeType', 'mercari_specifics.noBrand'].includes(row.path)));
  // Subcategories by display order, then the category's own "All ..." pick; a top-level category is not offered.
  assert.deepEqual(form.fields.find(row => row.label === 'Category').options,
    ['Tees', 'Kids > Girls tops & t-shirts > Girls (4+)', 'Kids > Girls tops & t-shirts']);
  assert.ok(!call(c, 'mercariCategory', listing).parent);
  assert.ok(call(c, 'mercariCategory', { form: { overrides: { categoryV2: { id: '3', displayPath: ['Kids'], hasChildren: true } } } }).parent);
});

// Mercari's sell page, read 2026-10-27: its sections, field names and order, and the size group headings.
test('Mercari form follows the sell page sections and order', () => {
  const c = loadWorker();
  const master = { ...MERCARI, itemSizeSubGroups: [{ id: 11, title: 'Standard', sizeIds: [1] }, { id: 12, title: 'Petite', sizeIds: [2] }],
    itemSizes: [{ id: 1, title: 'XS', subtitle: '(0-2)', itemSizeSubGroupId: 11 }, { id: 2, title: 'XS', subtitle: '(0-2)', itemSizeSubGroupId: 12 }] };
  const form = clean(call(c, 'mercariEditorForm', { direct_category_ids: { mercari: 1 }, title: 'A tee', price: 20 }, master));
  assert.deepEqual(form.fields.map(row => [row.section, row.label.replace(/ \*$/, '')]), [
    ['', ''],
    ['Product info', 'Title'], ['Product info', 'Description'], ['Product info', 'Category'], ['Product info', 'Brand'],
    ['Product info', 'Condition'], ['Product info', 'Size'],
    ['Shipping', 'Ships from:'], ['Shipping', 'Shipping method'],
    ['Shipping', 'lb'], ['Shipping', 'oz'], ['Shipping', 'Will your item fit in a shoebox?'],
    ['Shipping', 'Length'], ['Shipping', 'Width'], ['Shipping', 'Height'], ['Shipping', 'Skip dimension input'],
    ['Shipping', 'Shipping label'], ['Shipping', 'Offer buyers free shipping?'],
    ['Pricing', 'Item price'], ['Smart pricing', 'Adjusts price automatically'], ['Smart pricing', 'Minimum price'],
  ]);
  const rows = Object.fromEntries(form.fields.map(row => [row.label, row]));
  assert.deepEqual(rows.Condition.options, ['New', 'Like new', 'Good', 'Fair', 'Poor']);
  assert.equal(rows.Condition.presentation, 'radio-row');
  assert.equal(rows.Title.placeholder, 'What are you selling?');
  assert.equal(rows.Title.counter, true);
  assert.deepEqual(rows['Shipping method'].options, ['Prepaid label', 'Ship on your own']);
  assert.deepEqual(rows.Size.options, ['XS (0-2) · Standard', 'XS (0-2) · Petite']);
  assert.deepEqual(rows.Size.option_groups, { 'XS (0-2) · Standard': 'Standard', 'XS (0-2) · Petite': 'Petite' });
  // One question over lb and oz, each a quarter of the row. The three sides share the next question.
  assert.equal(rows.lb.group, 'How heavy will the package be?');
  assert.equal(rows.oz.group, rows.lb.group);
  assert.deepEqual(form.fields.filter(row => row.width === 'quarter').map(row => row.label),
    ['Ships from:', 'lb', 'oz', 'Minimum price']);
  assert.deepEqual(form.fields.filter(row => row.width === 'third').map(row => row.label), ['Length', 'Width', 'Height']);
  assert.deepEqual(rows['Will your item fit in a shoebox?'].options, ['Yes', 'No']);
  // No heading. The page's two lines are the hint, and any image type is accepted.
  assert.deepEqual([form.fields[0].path, form.fields[0].input_type, form.fields[0].help, form.fields[0].accept],
    ['marketplace_photos.mercari', 'photos', 'Add up to 12 photos\nOr drag and drop', undefined]);
  // A price turns Smart pricing on and fills the 85% floor, as the sell page does.
  assert.equal(rows['Adjusts price automatically'].value, true);
  assert.equal(rows['Minimum price'].value, 17);
});

// Read from Mercari's label picker on 2026-10-27: Yes sends a 14 x 10 x 5 in box, No sends the typed
// sides or none when they are skipped, and the weight goes in whole ounces.
test('Mercari package typed on its tab sets the weight and box the labels are read for', () => {
  const c = loadWorker();
  const listing = { direct_category_ids: { mercari: 1 }, weight_lb: 0, weight_oz: 8, package_dimensions_in: '10x8x4' };
  const rows = Object.fromEntries(clean(call(c, 'mercariEditorForm', listing, MERCARI)).fields.map(row => [row.path, row]));
  // The rows start from Item Details. The shoebox question stays unanswered, so nothing is sent for size.
  assert.deepEqual(['pounds', 'ounces', 'fitsShoebox', 'length', 'width', 'height'].map(key => rows[`mercari_specifics.package.${key}`].value),
    [0, 8, '', 10, 8, 4]);
  assert.equal(c.mercariWeightOz(listing), 8);
  assert.deepEqual(clean(c.mercariDimensions(listing)), {});
  const typed = pack => ({ ...listing, mercari_specifics: { package: pack } });
  assert.equal(c.mercariWeightOz(typed({ pounds: 1, ounces: 8 })), 24);
  assert.deepEqual(clean(c.mercariDimensions(typed({ fitsShoebox: 'yes' }))), { unit: 'INCH', length: 14, width: 10, height: 5 });
  assert.deepEqual(clean(c.mercariDimensions(typed({ fitsShoebox: 'no', length: 16, width: 12, height: 6 }))),
    { unit: 'INCH', length: 16, width: 12, height: 6 });
  // No, with the inputs still showing Item Details, sends those sides. Skip sends 0 x 0 x 0.
  assert.deepEqual(clean(c.mercariDimensions(typed({ fitsShoebox: 'no' }))), { unit: 'INCH', length: 10, width: 8, height: 4 });
  assert.deepEqual(clean(c.mercariDimensions(typed({ fitsShoebox: 'no', skipDimensions: true }))),
    { unit: 'INCH', length: 0, width: 0, height: 0 });
});

test('Depop read-back catches cleared attributes, old sizes and general choices the draft failed to keep', () => {
  const c = loadWorker();
  const listing = { description: 'Details', price: 16 };
  const fields = { attributes: {}, variant_set: null, variants: {}, age: [], style: ['casual'], condition: 'used_good', brand: 'nike' };
  const draft = { description: 'Details', price_amount: 16, attributes: { material: ['cotton'] }, variant_set: 12, variants: { 1: 1 },
    age: ['y2k'], style: ['sportswear'], condition: 'used_excellent', brand: { id: 'adidas' } };
  const mismatch = call(c, 'depopDraftMismatch', draft, listing, fields);
  assert.deepEqual(clean(mismatch).sort(), ['size', 'material', 'age', 'style', 'condition', 'brand'].sort());
  assert.deepEqual(clean(call(c, 'depopDraftMismatch', { ...draft, ...fields }, listing, fields)), []);
});

test('Mercari form and request share category-specific native size and condition IDs', () => {
  const c = loadWorker();
  const listing = { title: 'Tee', size: 'XL', condition: 'Good', direct_category_ids: { mercari: '1' },
    mercari_specifics: { nativeSizeId: '2' }, form: { sizes: [{ id: 1, name: 'XL' }, { id: 2, name: '2XL' }] } };
  const form = clean(call(c, 'mercariEditorForm', listing, MERCARI));
  assert.equal(form.fields.find(row => row.label === 'Size').value, 2);
  assert.equal(call(c, 'mercariSize', listing, 1).id, 2);
  assert.equal(form.fields.find(row => row.label === 'Size').options_source, 'marketplace');
  assert.ok(!form.fields.some(row => row.path === 'quantity' || row.path === 'primaryColor'));
  listing.mercari_specifics.nativeSizeId = '';
  assert.ok(call(c, 'mercariSize', listing, 1).problem);
});

test('form reader makes no marketplace writes and reports catalog failures', async () => {
  const c = loadWorker();
  const sent = [];
  c.sendBrowserResult = (_msg, result) => sent.push(clean(result));
  c.poshmarkCatalogData = async () => { throw new Error('Catalog unavailable'); };
  c.poshmarkShippingDiscounts = async () => [];
  c.fetch = async () => assert.fail('No marketplace write or unexpected read');
  await c.handleNativeMarketplaceForm({ payload: { marketplace: 'poshmark' } });
  assert.equal(sent[0].ok, false);
  assert.equal(sent[0].error, 'Catalog unavailable');
  c.mercariCatalogData = async () => MERCARI;
  c.mercariShippingClasses = async () => [];
  await c.handleNativeMarketplaceForm({ payload: { marketplace: 'mercari', listing: {} } });
  assert.equal(sent[1].form.marketplace, 'mercari');
});

test('form reader answers a stalled catalog once and ignores its late completion', async () => {
  const c = loadWorker();
  const sent = [];
  let timeout;
  let finish;
  c.sendBrowserResult = (_msg, result) => sent.push(clean(result));
  c.setTimeout = (callback, delay) => {
    assert.equal(delay, 20000);
    timeout = callback;
    return 42;
  };
  const cleared = [];
  c.clearTimeout = timer => cleared.push(timer);
  c.mercariCatalogData = () => new Promise(resolve => { finish = resolve; });
  c.mercariShippingClasses = async () => [];
  const pending = c.handleNativeMarketplaceForm({ payload: { marketplace: 'mercari' } });
  timeout();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].ok, false);
  assert.match(sent[0].error, /did not answer in time/);
  finish(MERCARI);
  await pending;
  assert.equal(sent.length, 1);
  assert.deepEqual(cleared, [42]);
});

// Rules read from Depop's sell page by picking every live category on 2026-10-27.
test('Depop form lists the sell page fields in its order, as the page shows them', () => {
  const c = loadWorker();
  const en = (en, us = en) => ({ name_i18n: { en, 'en-US': us } });
  const attributes = { ...DEPOP,
    department: [{ id: 'menswear', ...en('Men') }, { id: 'kidswear', ...en('Kids') }],
    group: [
      { id: 'bottoms', ...en('Bottoms'), department: ['menswear', 'kidswear'], product_types: [
        { id: 'jeans', ...en('Jeans'), attribute_ids: ['bottom-fit', 'bottom-style', 'body-fit', 'size-fit'] },
        { id: 'oxfords', ...en('Oxfords'), status: 'inactive', attribute_ids: [] },
        { id: 'skorts', ...en('Skorts'), department: ['kidswear'], attribute_ids: [] },
      ] },
    ],
    condition: { attributes: [{ id: 'brand_new', ...en('Brand new') }, { id: 'used_good', ...en('Used - Good') }] },
    colour: { attributes: [{ id: 'blue', ...en('Blue') }, { id: 'black', ...en('Black') }, { id: 'teal', ...en('Teal') }] },
    generic_attributes: [
      // Type: a men's-only field, so a kids' category does not show it.
      { id: 'bottom-fit', ...en('Type'), max_selection: 3, attributes: [{ id: 'ripped', ...en('Ripped'), department: ['menswear'] }] },
      // Fit: nameless and retired choices are not offered.
      { id: 'bottom-style', ...en('Fit'), max_selection: 2, attributes: [
        { id: 'straight', ...en('Straight '), department: ['menswear', 'kidswear'] }, { id: 'tapered', name_i18n: {} },
        { id: 'flare', ...en('Flare'), status: 'inactive' }] },
      // The page names this field and its choices in plain English, not the US name.
      { id: 'body-fit', ...en('Body fit', 'Size grouping'), max_selection: 2, attributes: [
        { id: 'tall', ...en('Tall'), department: ['menswear'] }, { id: 'maternity', ...en('Maternity'), department: ['womenswear'] }] },
      { id: 'size-fit', ...en('Sizing'), status: 'inactive', attributes: [{ id: 'runs-large', ...en('Runs large') }] },
    ] };
  const listing = { direct_category_ids: { depop: 'menswear_jeans' }, description: 'Dark wash', tags: ['denim'],
    primaryColor: 'Blue', secondaryColor: 'Black' };
  const form = clean(call(c, 'depopEditorForm', listing, attributes, [{ title: 'Medium' }]));
  assert.deepEqual(form.fields.map(row => row.label), [
    '', 'Description', 'Category', 'Type', 'Fit', 'Body fit', 'Brand', 'Condition', 'Color', 'Size', 'Quantity', 'SKU',
    'Source', 'Age', 'Style', 'Item price', 'Promote your item in search to help it sell faster. Pay an extra 12% fee, only if you sell.', 'Package size', 'Offer worldwide shipping', 'Worldwide Shipping',
  ]);
  const rows = Object.fromEntries(form.fields.map(row => [row.label, row]));
  // Boost costs a fee on the sale, so it is off until the seller turns it on.
  assert.deepEqual([rows['Promote your item in search to help it sell faster. Pay an extra 12% fee, only if you sell.'].path, rows['Promote your item in search to help it sell faster. Pay an extra 12% fee, only if you sell.'].input_type, rows['Promote your item in search to help it sell faster. Pay an extra 12% fee, only if you sell.'].value], ['depop_specifics.boost', 'boolean', false]);
  assert.deepEqual(rows.Category.options, ['Men > Bottoms > Jeans', 'Kids > Bottoms > Jeans', 'Kids > Bottoms > Skorts']);
  assert.deepEqual(rows.Fit.options, ['Straight']);
  assert.deepEqual(rows['Body fit'].options, ['Tall', 'Maternity']);
  assert.deepEqual(rows.Condition.options, ['Brand new', 'Used - Good']);
  assert.deepEqual(rows.Color.options, ['Blue', 'Black']);
  assert.deepEqual(rows.Color.value, ['blue', 'black']);
  assert.equal(rows.Color.max_values, 2);
  assert.match(rows.Description.help, /#denim/);
  assert.equal(rows['Item price'].path, 'price');
  // The page's headings and row layout.
  const headings = [];
  for (const row of form.fields) if (headings.at(-1) !== row.section) headings.push(row.section);
  assert.deepEqual(headings, ['Photos', 'Description', 'Info', 'Enhance your listing', 'Item price', 'Boost your listing', 'Shipping']);
  // Depop's photo area: its hint and the caption under each slot.
  assert.deepEqual([form.fields[0].path, form.fields[0].help, form.fields[0].slot_labels, form.fields[0].accept],
    ['marketplace_photos.depop', 'Add up to 8 photos in JPEG or PNG format.',
      ['Cover photo', 'Front', 'Back', 'Side', 'Label', 'Detail', 'Flaw'], 'image/jpeg,image/png,.jpg,.jpeg,.png']);
  assert.deepEqual(form.fields.filter(row => row.width).map(row => [row.label, row.width]), [['Size', 'quarter'], ['Quantity', 'quarter'], ['SKU', 'half']]);
  assert.equal(rows.Type.help, 'Select up to 3');
  assert.equal(rows.Color.help, 'Select up to two colors');
  assert.equal(rows.Age.help, 'Do you know when this item was made? Add up to 1');
  const kids = clean(call(c, 'depopEditorForm', { ...listing, direct_category_ids: { depop: 'kidswear_jeans' } }, attributes, []));
  assert.ok(!kids.fields.some(row => row.label === 'Type'));
  // The form's Color picks are what direct List posts.
  assert.deepEqual(clean(call(c, 'depopFormFields', { ...listing, depop_specifics: { colour: ['navy'] } }).colour), ['navy']);
});

test('Depop Update sends Boost the way Depop\'s product request does, only once the seller set it', () => {
  const c = loadWorker();
  const fields = { product_type: 'jeans', attributes: {}, variants: {}, shipping_methods: [] };
  const product = { picture_ids: [11] };
  const body = boost => clean(call(c, 'depopLiveWriteBody', product, { price: 20, depop_specifics: boost === undefined ? {} : { boost } }, fields));
  assert.equal(body(undefined).boost, undefined);
  assert.deepEqual(body(true).boost, { status: 'active' });
  assert.deepEqual(body(false).boost, { status: 'inactive' });
});
