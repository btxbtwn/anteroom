const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { loadWorker, call } = require('./worker-context');

test('a catalog is read once, kept for six hours, and a failed read is not kept', async () => {
  const c = loadWorker();
  let reads = 0;
  const read = async () => { reads += 1; return { sizes: reads }; };
  assert.deepEqual(await c.marketplaceCatalog('depop:sizes', read), { sizes: 1 });
  assert.deepEqual(await c.marketplaceCatalog('depop:sizes', read), { sizes: 1 });
  assert.equal(reads, 1);
  vm.runInContext("marketplaceCatalogs.get('depop:sizes').at -= MARKETPLACE_CATALOG_TTL_MS", c);
  assert.deepEqual(await c.marketplaceCatalog('depop:sizes', read), { sizes: 2 });
  await assert.rejects(c.marketplaceCatalog('mercari:brands', async () => { throw new Error('offline'); }), /offline/);
  assert.deepEqual(await c.marketplaceCatalog('mercari:brands', async () => ['Nike']), ['Nike']);
});

test('a failed catalog read says what to do and is always safe to retry', async () => {
  const c = loadWorker();
  const failure = (code, options) => c.marketplaceCatalogFailure('depop', 'sizes', Object.assign(new Error('Depop says no'),
    { code, status: 403 }), options);
  const blocked = failure('signed_out');
  assert.equal(blocked.code, 'challenge', 'a public catalog answering 401/403 is a browser check, not a sign-out');
  assert.match(blocked.message, /Depop blocked Anteroom from reading its sizes/);
  assert.equal(blocked.rejected, true);
  assert.equal(blocked.status, 403);
  assert.equal(failure('signed_out', { signedIn: true }).code, 'signed_out');
  assert.equal(failure('signed_out', { signedIn: true }).message, 'Depop says no');
  assert.equal(failure('uncertain').code, 'network');
  assert.match(failure('uncertain').message, /could not read Depop's sizes, so nothing was listed/);
  assert.match(failure('schema_drift').message, /Depop's sizes changed/);
  assert.equal(c.marketplaceCatalogFailure('mercari', 'brands', null).code, 'network');
  await assert.rejects(c.marketplaceCatalogRead('depop', 'sizes', async () => { throw Object.assign(new Error('x'), { code: 'rate_limited' }); }),
    error => error.code === 'rate_limited' && error.rejected === true);
  const missing = Object.assign(new Error('Pick a size'), { code: 'missing_fields' });
  await assert.rejects(c.marketplaceCatalogRead('depop', 'sizes', async () => { throw missing; }), error => error === missing);
  assert.equal(await c.marketplaceCatalogRead('depop', 'sizes', async () => 'ok'), 'ok');
});

test('names compare without case, accents or punctuation', () => {
  const c = loadWorker();
  assert.equal(c.marketplaceNameKey('Levi’s'), c.marketplaceNameKey("LEVI'S"));
  assert.equal(c.marketplaceNameKey('Abercrombie & Fitch'), 'abercrombieandfitch');
  assert.equal(c.marketplaceNameKey('Émilie'), 'emilie');
  assert.equal(c.marketplaceNameKey(null), '');
});

test('a seller size is read every way a catalog may spell it, most literal first', () => {
  const c = loadWorker();
  assert.deepEqual(call(c, 'sizeCandidates', 'XL (18/20)'), ['XL (18/20)', 'XL', '18/20', '18', '20']);
  assert.ok(call(c, 'sizeCandidates', '32x30').includes('32'));
  assert.ok(call(c, 'sizeCandidates', 'W32 L30').includes('32'));
  assert.deepEqual(call(c, 'sizeCandidates', 'US 10'), ['US 10', '10']);
  assert.deepEqual(call(c, 'sizeCandidates', '2XL'), ['2XL', 'XXL']);
  assert.deepEqual(call(c, 'sizeCandidates', 'XXXL'), ['XXXL', '3XL']);
  assert.ok(call(c, 'sizeCandidates', 'Extra Large').includes('XL'));
  assert.ok(call(c, 'sizeCandidates', 'One Size Fits All').includes('ONE SIZE'));
  assert.deepEqual(call(c, 'sizeCandidates', '  '), []);
});

test('catalog sizes match with or without their waist words, units and petite marker', () => {
  const c = loadWorker();
  const entry = (id, labels, options) => {
    const keys = c.sizeEntryKeys(labels, options);
    return { id, keys };
  };
  const entries = [entry('waist', ['Waist 36"']), entry('girl', ['M (Girl)']), entry('petite', ['4P'], { petite: true }), entry('xl', ['XL'])];
  assert.deepEqual(c.matchSize('36', entries).map(found => found.id), ['waist']);
  assert.deepEqual(c.matchSize('M', entries).map(found => found.id), ['girl']);
  assert.deepEqual(c.matchSize('4', entries).map(found => found.id), ['petite']);
  assert.deepEqual(c.matchSize('XL (18/20)', entries).map(found => found.id), ['xl']);
  assert.equal(c.matchSize('XXL', entries).length, 0);
  // An exact spelling wins over a loose one.
  const exact = [entry('loose', ['Waist 30']), entry('tight', ['30'])];
  assert.deepEqual(c.matchSize('30', exact).map(found => found.id), ['tight']);
  // Two loose matches come back together, so the caller can refuse the ambiguity.
  assert.deepEqual(c.matchSize('30', [entry('a', ['Waist 30']), entry('b', ['30 in'])]).map(found => found.id), ['a', 'b']);
});

test('department, fit and tags come only from what the listing says', () => {
  const c = loadWorker();
  assert.equal(c.listingKidsGender({ department: 'Boys' }), 'boys');
  assert.equal(c.listingKidsGender({ category_path: 'Kids > Girls\' > Tops' }), 'girls');
  assert.equal(c.listingKidsGender({ department: 'Boys and girls' }), null, 'never guessed');
  assert.equal(c.listingKidsGender({ marketplace_categories: { depop: 'Kidswear > Boys' } }), 'boys');
  assert.equal(c.listingKidsGender({}), null);
  assert.equal(c.listingSizeFit({ sizeType: 'Regular' }), null);
  assert.equal(c.listingSizeFit({ sizeType: 'Regular', poshmark_specifics: { sizeType: 'Petite' } }), 'petite');
  assert.equal(c.listingSizeFit({ ebay_specifics: { sizeType: 'Big & Tall' } }), 'big_&_tall');
  assert.equal(c.listingSizeFit({ sizeType: 'Tall' }), 'tall');
  assert.equal(c.listingSizeFit({ sizeType: 'Plus' }), 'plus');
  assert.equal(c.listingSizeFit({ sizeType: 'Juniors' }), 'juniors');
  assert.equal(c.listingSizeFit({}), null);
  assert.deepEqual(call(c, 'listingTags', { tags: ' vintage, denim,vintage,, ' }), ['vintage', 'denim']);
  assert.deepEqual(call(c, 'listingTags', { tags: ['y2k', ' y2k', null] }), ['y2k']);
  assert.deepEqual(call(c, 'listingTags', {}), []);
});
