const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, call } = require('./worker-context');

const DEPT = '01008c10d97b4e1245005764';
const SHIRTS = '07008c10d97b4e1245005764';
const TEES = 'a'.repeat(24);
const CATALOG = { catalog: { departments: [{ id: DEPT, display: 'Men', categories: [{ id: SHIRTS, display: 'Shirts',
  category_features: [{ id: TEES, display: 'Tees - Short Sleeve' }],
  size_sets: [{ name: 'Standard', tags: ['standard'], size_systems: { us: { sizes: [
    { id: 'M', short: 'M', display: 'M' }, { id: 'XL', short: 'XL', display: 'XL' },
  ] } } }] }] }] } };
const ATTRIBUTES = {
  category_size_mapping: [],
  size_sets: [],
  department: [{ id: 'menswear' }, { id: 'womenswear' }],
  group: [
    { id: 'tops', department: ['menswear', 'womenswear'], product_types: [{ id: 'tshirts' }] },
    { id: 'dresses', department: ['womenswear'], product_types: [{ id: 'dresses' }] },
  ],
};

test('a direct check names the live category and a size or parcel List would refuse', () => {
  const c = loadWorker();
  const named = call(c, 'chromeDirectPreview', {
    size: 'M',
    marketplace_categories: { poshmark: 'Men > Shirts > Tees - Short Sleeve', depop: 'Men > Tops > T-Shirts' },
    marketplace_category_ids: { poshmark: 'stale', depop: 'old_leaf' },
    depop_specifics: { parcelSize: 'Medium' },
  }, { catalog: CATALOG, attributes: ATTRIBUTES, parcels: [{ title: 'Medium' }, { title: 'Small' }] });
  assert.equal(named.categories.poshmark, `${DEPT}/${SHIRTS}/${TEES}`);
  assert.equal(named.categories.depop, 'menswear_tshirts');
  assert.deepEqual(named.gaps.poshmark, []);
  assert.deepEqual(named.gaps.depop, []);

  const refused = call(c, 'chromeDirectPreview', {
    size: '40R',
    direct_category_ids: { poshmark: `${DEPT}/${SHIRTS}/${TEES}`, depop: 'womenswear_dresses' },
    marketplace_category_ids: { depop: 'menswear_tshirts' },
    form: { overrides: { categoryV2: { id: 'menswear_tshirts', path: ['menswear', 'menswear_tops', 'menswear_tshirts'] } } },
    depop_specifics: { parcelSize: 'Under 1lb' },
  }, { catalog: CATALOG, attributes: ATTRIBUTES, parcels: [{ title: 'Medium' }] });
  assert.equal(refused.categories.poshmark, `${DEPT}/${SHIRTS}/${TEES}`);
  assert.equal(refused.categories.depop, 'womenswear_dresses');
  assert.equal(refused.gaps.poshmark[0].blocking, true);
  assert.match(refused.gaps.poshmark[0].message, /size/);
  assert.match(refused.gaps.depop[0].message, /Under 1lb/);
});

test('one size categories and a live parcel title are not refused', () => {
  const c = loadWorker();
  const catalog = { catalog: { departments: [{ id: DEPT, display: 'Women', categories: [{ id: SHIRTS, display: 'Accessories',
    size_sets: [{ tags: ['standard'], size_systems: { us: { sizes: [{ id: 'OS', display: 'OS' }] } } }] }] }] } };
  const preview = call(c, 'chromeDirectPreview', {
    marketplace_categories: { poshmark: 'Women > Accessories' },
    depop_specifics: { parcelSize: 'Oversize' },
  }, { catalog, parcels: [{ title: 'Oversize' }] });
  assert.deepEqual(preview.gaps.poshmark, []);
  assert.deepEqual(preview.gaps.depop, []);
});

test('Depop preflight uses the same category size check as draft creation', () => {
  const c = loadWorker();
  const attributes = { ...ATTRIBUTES,
    category_size_mapping: [{ department: 'menswear', product_type: 'tshirts', size_set_by_region: { US: 12 } }],
    size_sets: [{ id: 12, sizes: [{ id: 4, name_i18n: { 'en-US': 'M' } }] }],
  };
  const listing = { direct_category_ids: { depop: 'menswear_tshirts' } };
  for (const size of ['', '40R']) {
    const preview = call(c, 'chromeDirectPreview', { ...listing, size }, { attributes });
    assert.equal(preview.gaps.depop[0].field, 'size');
    assert.equal(preview.gaps.depop[0].blocking, true);
  }
  assert.deepEqual(call(c, 'chromeDirectPreview', { ...listing, size: 'M' }, { attributes }).gaps.depop, []);
  assert.deepEqual(call(c, 'chromeDirectPreview', listing, { attributes: ATTRIBUTES }).gaps.depop, []);
});
