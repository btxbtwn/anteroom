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
  const form = call(c, 'poshmarkEditorForm', { ...POSH_LISTING, brand: 'Nike' }, POSH);
  const fields = Object.fromEntries(form.fields.map(row => [row.path, row]));
  assert.equal(fields['poshmark_specifics.title'].value, 'A tee');
  assert.equal(fields['poshmark_specifics.description'].value, 'Details');
  assert.equal(fields['poshmark_specifics.brand'].value, 'Nike');
  assert.equal(fields.price.path, 'price');
  const overridden = call(c, 'poshmarkEditorForm', {
    ...POSH_LISTING, brand: 'Nike',
    poshmark_specifics: { title: 'Posh title', description: '', brand: 'Posh brand' },
  }, POSH);
  const rows = Object.fromEntries(overridden.fields.map(row => [row.path, row]));
  assert.equal(rows['poshmark_specifics.title'].value, 'Posh title');
  assert.equal(rows['poshmark_specifics.description'].value, '');
  assert.equal(rows['poshmark_specifics.brand'].value, 'Posh brand');
});

test('Poshmark form uses native category, group and size IDs and only supported fields', () => {
  const c = loadWorker();
  const form = clean(call(c, 'poshmarkEditorForm', POSH_LISTING, POSH, ['Casual'], [{ id: 'discount-1', label: 'Buyer Shipping: Free' }]));
  const fields = Object.fromEntries(form.fields.map(row => [row.path, row]));
  assert.equal(form.native, true);
  assert.deepEqual(fields['poshmark_specifics.shippingDiscountId'].option_values, { 'Buyer Shipping: Free': 'discount-1' });
  assert.deepEqual(fields['poshmark_specifics.availability'].option_values, { 'For Sale': 'available', 'Not For Sale': 'not_for_sale' });
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
  assert.equal(fields['poshmark_specifics.smartSell.enabled'].input_type, 'boolean');
  assert.ok(fields.cost.locked);
  assert.ok(!form.fields.some(row => /shipping label|size scale/i.test(row.label)));
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
  const form = call(c, 'poshmarkEditorForm', listing, POSH);
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

test('Mercari ship on your own keeps the package rows and records seller-paid shipping', () => {
  const c = loadWorker();
  const form = call(c, 'mercariEditorForm', { title: 'A tee', direct_category_ids: { mercari: 1 },
    mercari_specifics: { shippingMethod: 'SOYO', shippingPayerId: 1 } }, MERCARI);
  const fields = Object.fromEntries(form.fields.map(row => [row.path, row]));
  assert.equal(fields['mercari_specifics.shippingMethod'].value, 'SOYO');
  assert.equal(fields['mercari_specifics.shippingPayerId'].value, 2);
  assert.notEqual(fields['mercari_specifics.shippingPayerId'].locked, true);
  assert.deepEqual(fields['mercari_specifics.shippingPayerId'].option_values, { Buyer: '1', Seller: '2' });
  assert.ok(fields['mercari_specifics.shippingClassId']);
  assert.ok(fields.weight_oz);
  assert.ok(fields.package_dimensions_in);
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

test('Mercari shows the seller’s brand and an unavailable parent label without offering the parent', () => {
  const c = loadWorker();
  const listing = { brand: 'GB girls', form: { overrides: { brand: '30138', categoryV2: {
    id: '1877', displayPath: ['Kids', 'Girls tops & t-shirts'], hasChildren: true,
  } } } };
  const form = clean(call(c, 'mercariEditorForm', listing, MERCARI));
  const brand = form.fields.find(row => row.label === 'Brand');
  assert.equal(brand.path, 'mercari_specifics.brand');
  assert.equal(brand.value, 'GB Girls');
  assert.equal(brand.option_values['No brand'], 'No Brand/Not sure');
  assert.ok(form.fields.some(row => row.path === 'mercari_specifics.sizeType'));
  const category = form.fields.find(row => row.label === 'Category');
  assert.equal(category.option_values['Kids > Girls tops & t-shirts'], '1877');
  assert.ok(!category.options.includes('Kids > Girls tops & t-shirts'));
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
