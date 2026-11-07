// Every General size, condition and color, through the code direct List sends
// to Poshmark, Mercari and Depop, checked against each marketplace's own option
// lists as captured from its live forms (skills/list-this/references/).
//
// An answer is either one of the category's options (its id, with the label it
// belongs to) or an explicit "no equivalent": a problem the seller sees, or no
// value at all. Never free text, never a number read as an id, never the wrong
// option when the list spells the size exactly. Known gaps are listed below, one
// entry per value; the list can only shrink. A refreshed form sweep updates the
// captured lists this test reads.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { loadWorker, call } = require('./worker-context');

const REFERENCES = path.resolve(__dirname, '../../skills/list-this/references');
const read = name => JSON.parse(fs.readFileSync(path.join(REFERENCES, name), 'utf8'));
const GENERAL = read('vendoo-dropdown-options.json').forms.vendoo;
const POSHMARK = read('poshmark-native-form.json');
const MERCARI = read('mercari-native-catalog.json');
const DEPOP = read('depop-native-catalog.json');
const MATRIX = require('./fixtures/marketplace-value-matrix.json');

const c = loadWorker();

// General's size list, split the way the families in the fixture name it.
const tailored = size => /^(IT|EU|FR) /.test(size);
const SIZE_GROUPS = {
  ...MATRIX.size_groups,
  general_letters: GENERAL.usSize.filter(size => !/^\d+$/.test(size) && !tailored(size)),
  general_numbers: GENERAL.usSize.filter(size => /^\d+$/.test(size)),
  general_tailored: GENERAL.usSize.filter(tailored),
};
const familySizes = family => [...new Set(family.sizes.flatMap(group => SIZE_GROUPS[group]))];
const short = family => family.general.split(' > ').slice(-2).join(' > ');
const same = (left, right) => String(left ?? '').trim().toLowerCase() === String(right ?? '').trim().toLowerCase();

// Values the mapping cannot land yet, one per value, with the reason. Fixing
// one makes its entry fail here until it is removed.
const KNOWN_GAPS = {};

function checkGaps(marketplace, found) {
  const known = KNOWN_GAPS[marketplace] || {};
  const unexpected = Object.keys(found).filter(key => !(key in known)).map(key => `${key}: ${found[key]}`);
  const fixed = Object.keys(known).filter(key => !(key in found));
  assert.deepEqual(unexpected, [], `New ${marketplace} mismatches`);
  assert.deepEqual(fixed, [], `Fixed ${marketplace} gaps: remove them from KNOWN_GAPS`);
}

test('every General size is in a matrix family', () => {
  const covered = new Set(MATRIX.families.flatMap(familySizes));
  assert.deepEqual(GENERAL.usSize.filter(size => !covered.has(size)), []);
});

// --- Poshmark ---------------------------------------------------------------

// Poshmark's catalog_v2 shape, rebuilt from the captured create-listing form.
const POSHMARK_TAGS = { Standard: 'standard', Plus: 'plus', Petite: 'petite', Juniors: 'juniors', Maternity: 'maternity',
  'Big & Tall': 'big_&_tall', Baby: 'baby', Girls: 'girls', Boys: 'boys' };
const poshmarkCatalog = (() => {
  const row = POSHMARK.fields.find(field => field.path === 'direct_category_ids.poshmark');
  const departments = row.cascade.map(department => ({ id: department.id, display: department.label, categories: [] }));
  for (const [index, department] of row.cascade.entries()) {
    const categories = department.items.flatMap(item => item.type === 'group' ? item.items : [item]);
    for (const category of categories) {
      const id = category.value.split('/')[1];
      const groups = POSHMARK.inventory_groups[category.value] || [];
      departments[index].categories.push({
        id, display: category.label,
        category_features: category.children.filter(child => child.value.split('/').length === 3)
          .map(child => ({ id: child.value.split('/')[2], display: child.label })),
        size_sets: groups.filter(group => group.id !== 'custom').map(group => ({
          id: group.id, name: group.label, tags: [POSHMARK_TAGS[group.label]],
          size_systems: { us: { sizes: (group.systems?.us || group.sizes).map(size => ({ id: size.id, short: size.id, display: size.label })) } },
        })),
      });
    }
  }
  return { catalog: { departments }, colors: POSHMARK.fields.find(field => field.path === 'poshmark_specifics.colors').options.map(name => ({ name })) };
})();
const POSHMARK_CONDITIONS = Object.fromEntries(vm.runInContext('POSHMARK_CONDITION_CHOICES', c).map(([label, code]) => [code, label]));

test('Poshmark: every General condition is one of its four conditions', () => {
  assert.deepEqual(Object.values(POSHMARK_CONDITIONS),
    POSHMARK.fields.find(field => field.path === 'poshmark_specifics.condition').options);
  for (const condition of GENERAL.condition) {
    const code = call(c, 'poshmarkCondition', { condition });
    assert.ok(code in POSHMARK_CONDITIONS, `${condition} -> ${code}`);
  }
});

test('Poshmark: every General color is its own color or left off', () => {
  const offered = poshmarkCatalog.colors.map(color => color.name);
  for (const color of GENERAL.primaryColor) {
    const colors = call(c, 'poshmarkColors', { primaryColor: color }, poshmarkCatalog);
    assert.ok(colors.every(name => offered.includes(name)), `${color} -> ${colors}`);
    if (offered.includes(color)) assert.deepEqual(colors, [color]);
  }
});

function poshmarkSizeFor(nativeId, listing) {
  const found = call(c, 'poshmarkCategory', poshmarkCatalog, { ...listing, direct_category_ids: { poshmark: nativeId } });
  assert.ok(found, `Poshmark category ${nativeId}`);
  const kids = found.department.display === 'Kids';
  return { result: call(c, 'poshmarkSize', found.category, listing, { kids, leaf: found.leaf }), category: found.category };
}

test('Poshmark: every General size lands on a size its category offers, or none', () => {
  const found = {};
  for (const family of MATRIX.families) {
    const native = family.native.poshmark;
    if (!native) continue;
    for (const size of familySizes(family)) {
      const listing = { size, category_path: family.general };
      const { result, category } = poshmarkSizeFor(native.id, listing);
      const key = `${short(family)} | ${size}`;
      if (!result || result.problem) {
        // Only the sets this listing's department and fit can use: a boys' top is not offered a girls' 6X.
        const gender = /\bboys\b/i.test(family.general) ? 'boys' : /\bgirls\b/i.test(family.general) ? 'girls' : '';
        const usable = category.size_sets.filter(set => ['standard', gender].includes(set.tags[0]));
        const exact = usable.some(set => set.size_systems.us.sizes.some(option => same(option.display, size)));
        if (exact) found[key] = `no size, though the category lists ${size}`;
        continue;
      }
      const set = category.size_sets.find(entry => entry.id === result.set.id);
      if (!set || !set.size_systems.us.sizes.some(option => option.id === result.size.id)) {
        found[key] = `${result.size.id} is not in ${set?.name || 'any'} set`;
      }
    }
  }
  checkGaps('poshmark', found);
});

// --- Mercari ----------------------------------------------------------------

const MERCARI_CONDITIONS = MERCARI.fields.find(field => field.path === 'mercari_specifics.condition');

test('Mercari: every General condition is one of its five conditions', () => {
  const ids = new Set(Object.values(MERCARI_CONDITIONS.option_values).map(Number));
  for (const condition of GENERAL.condition) {
    const id = call(c, 'mercariConditionId', condition);
    assert.ok(ids.has(id), `${condition} -> ${id}`);
  }
});

// The category's sizes as Studio attaches them before List, read once per category.
const mercariForms = new Map();
function mercariSizeFor(nativeId, listing) {
  if (!mercariForms.has(nativeId)) {
    mercariForms.set(nativeId, call(c, 'mercariCatalogListing', { direct_category_ids: { mercari: nativeId } }, MERCARI).form);
  }
  const form = mercariForms.get(nativeId);
  const withSizes = { ...listing, direct_category_ids: { mercari: nativeId }, form };
  return { result: call(c, 'mercariSize', withSizes, Number(nativeId)), sizes: form.sizes };
}

test('Mercari: every General size lands on a size id its category offers, or none', () => {
  const found = {};
  for (const family of MATRIX.families) {
    const native = family.native.mercari;
    if (!native) continue;
    for (const size of familySizes(family)) {
      const { result, sizes } = mercariSizeFor(native.id, { size });
      const key = `${short(family)} | ${size}`;
      if (result.id == null) {
        if (sizes.some(option => same(option.name, size))) found[key] = `no size, though the category lists ${size}`;
        continue;
      }
      const option = sizes.find(entry => Number(entry.id) === result.id);
      if (!option) found[key] = `id ${result.id} is not one of the category's sizes`;
      else if (sizes.some(entry => same(entry.name, size)) && !same(option.name, size)) {
        found[key] = `${option.name}, though the category lists ${size}`;
      }
    }
  }
  checkGaps('mercari', found);
});

// --- Depop ------------------------------------------------------------------

const DEPOP_CONDITIONS = new Set(Object.values(DEPOP.fixed_option_values.condition));
const DEPOP_COLOURS = new Set(Object.values(DEPOP.fixed_option_values.colour));

test('Depop: every General condition is one of its five conditions', () => {
  for (const condition of GENERAL.condition) {
    const slug = call(c, 'depopCondition', condition);
    assert.ok(DEPOP_CONDITIONS.has(slug), `${condition} -> ${slug}`);
  }
});

test('Depop: every General color is its own colour or left off', () => {
  for (const color of GENERAL.primaryColor) {
    const fields = call(c, 'depopFormFields', { primaryColor: color }, {});
    const colours = fields.colour || [];
    assert.ok(colours.every(slug => DEPOP_COLOURS.has(slug)), `${color} -> ${colours}`);
    const label = Object.keys(DEPOP.fixed_option_values.colour).find(name => same(name, color));
    if (label) assert.deepEqual(colours, [DEPOP.fixed_option_values.colour[label]]);
  }
});

// Depop's size set for one captured category: its labels, numbered in order.
function depopSizeFor(nativeId, listing) {
  const category = DEPOP.categories.find(row => row.id === nativeId);
  const labels = category.fields.find(field => field.id === 'variants')?.options || [];
  const [department, productType] = nativeId.split(/_(.+)/);
  const attributes = { category_size_mapping: [{ department, product_type: productType, size_set_by_region: { US: 1 } }],
    size_sets: [{ id: 1, sizes: labels.map((name, index) => ({ id: index + 1, name })) }] };
  const result = call(c, 'depopSize', listing, { department, productType, leaf: nativeId }, attributes);
  return { result, labels };
}

test('Depop: every General size lands on a size its category offers, or none', () => {
  const found = {};
  for (const family of MATRIX.families) {
    const native = family.native.depop;
    if (!native) continue;
    for (const size of familySizes(family)) {
      const { result, labels } = depopSizeFor(native.id, { size });
      const key = `${short(family)} | ${size}`;
      if (result.id == null) {
        if (labels.some(label => same(label, size))) found[key] = `no size, though the category lists ${size}`;
        continue;
      }
      const label = labels[result.id - 1];
      if (!label) found[key] = `id ${result.id} is not one of the category's sizes`;
      else if (label === 'Other') found[key] = 'Other';
      else if (labels.some(entry => same(entry, size)) && !same(label, size)) found[key] = `${label}, though the category lists ${size}`;
    }
  }
  checkGaps('depop', found);
});

// --- Past regressions, each a value one marketplace once rejected or misread ----

const family = name => MATRIX.families.find(entry => entry.general.endsWith(name));

test('regressions: sizes', () => {
  const jeans = family("Men's Clothing > Jeans");
  const womensJeans = family("Women's Clothing > Jeans");
  const kidsTop = family("Boys' Clothing (Sizes 4 & Up) > Tops, Shirts & T-Shirts");
  const depop = (fam, size) => {
    const { result, labels } = depopSizeFor(fam.native.depop.id, { size });
    return result.id ? labels[result.id - 1] : result.problem;
  };
  const mercari = (fam, size, extra = {}) => {
    const { result, sizes } = mercariSizeFor(fam.native.mercari.id, { size, ...extra });
    return result.id ? sizes.find(entry => Number(entry.id) === result.id).name : result.problem;
  };
  const poshmark = (fam, size, extra = {}) => {
    const { result } = poshmarkSizeFor(fam.native.poshmark.id, { size, category_path: fam.general, ...extra });
    return result?.size ? `${result.size.display}:${result.set.name}` : result?.problem || null;
  };
  // #981: Depop lists a man's 36 waist as 36".
  assert.equal(depop(jeans, '36'), '36"');
  // #624: a women's 28 stays 28 where Depop offers both 28 and 28".
  assert.equal(depop(womensJeans, '28'), '28');
  // #767: XXL and 2XL are one size, spelled each marketplace's way.
  assert.equal(depop(family("Men's Clothing > Shirts > T-Shirts"), '2XL'), 'XXL');
  // #622: a bare number is a size label, never read as an option id.
  const { labels } = depopSizeFor(womensJeans.native.depop.id, {});
  for (const size of ['3', '15', '17']) {
    const landed = depop(womensJeans, size);
    assert.ok(landed === size || /offers/.test(landed), `${size} -> ${landed}`);
    assert.notEqual(landed, labels[Number(size) - 1]);
  }
  // #892 / #907: a tag reading 6 or S is Mercari's numbered size.
  assert.equal(mercari(family("Women's Clothing > Tops"), '6'), 'S (4-6)');
  assert.equal(mercari(kidsTop, '6'), 'S (6-7)');
  assert.equal(mercari(kidsTop, 'XL (18/20)'), '2XL (18)');
  // #689: a tag's "Large (12/14)" is the general L.
  assert.equal(poshmark(family("Women's Clothing > Tops"), 'Large (12/14)'), 'L:Standard');
  // #623: a bare waist is the waist size, in the regular group.
  assert.equal(poshmark(jeans, '32'), 'Waist 32:Standard');
  // #1183: the Poshmark size group sent is the group's id, never its label.
  const { result } = poshmarkSizeFor(jeans.native.poshmark.id, { size: '32' });
  assert.notEqual(result.set.id, 'Standard');
  assert.match(result.set.id, /^[0-9a-f]{24}$/);
});

test('regressions: conditions and colors', () => {
  // #370 / #385: Poshmark, Mercari and Depop get their own condition codes.
  assert.equal(call(c, 'poshmarkCondition', { condition: 'Pre-Owned - Good' }), 'ug');
  assert.equal(call(c, 'poshmarkCondition', { condition: 'New With Tags/Box' }), 'nwt');
  assert.equal(call(c, 'mercariConditionId', 'New Without Tags/Box'), 2);
  assert.equal(call(c, 'mercariConditionId', 'Poor (Major flaws)'), 5);
  assert.equal(call(c, 'depopCondition', 'Pre-Owned - Excellent'), 'used_excellent');
  // #433: Gray and Beige are Poshmark's Gray and Tan, Depop's grey and tan.
  assert.deepEqual(call(c, 'poshmarkColors', { primaryColor: 'Grey', secondaryColor: 'Beige' }, poshmarkCatalog), ['Gray', 'Tan']);
  assert.deepEqual(call(c, 'depopFormFields', { primaryColor: 'Gray', secondaryColor: 'Multicolor' }, {}).colour, ['grey', 'multi']);
  // #390: Multicolor is not a Poshmark color, so it is left off, not sent.
  assert.deepEqual(call(c, 'poshmarkColors', { primaryColor: 'Multicolor', secondaryColor: 'Blue' }, poshmarkCatalog), ['Blue']);
});
