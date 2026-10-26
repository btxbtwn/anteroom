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

test('Poshmark form uses native category, group and size IDs and only supported fields', () => {
  const c = loadWorker();
  const form = clean(call(c, 'poshmarkEditorForm', POSH_LISTING, POSH, ['Casual']));
  const fields = Object.fromEntries(form.fields.map(row => [row.path, row]));
  assert.equal(form.native, true);
  assert.equal(fields['direct_category_ids.poshmark'].value, 'men/shirts');
  assert.deepEqual(fields['poshmark_specifics.sizeSetId'].option_values, { standard: 'standard', 'big_&_tall': 'tall' });
  assert.equal(fields['poshmark_specifics.nativeSizeId'].value, 'M');
  assert.deepEqual(fields['poshmark_specifics.nativeSizeId'].options, ['M', 'XL']);
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
  assert.deepEqual(clean(form.fields.find(row => row.label === 'Size').options), []);
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
  assert.equal(form.fields.find(row => row.label === 'Brand').value, 'GB Girls');
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
  c.fetch = async () => assert.fail('No marketplace write or unexpected read');
  await c.handleNativeMarketplaceForm({ payload: { marketplace: 'poshmark' } });
  assert.equal(sent[0].ok, false);
  assert.equal(sent[0].error, 'Catalog unavailable');
  c.mercariCatalogData = async () => MERCARI;
  c.mercariShippingClasses = async () => [];
  await c.handleNativeMarketplaceForm({ payload: { marketplace: 'mercari', listing: {} } });
  assert.equal(sent[1].form.marketplace, 'mercari');
});
