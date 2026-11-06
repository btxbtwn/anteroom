const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker } = require('./worker-context');

test('the sold search asks eBay for completed, pre-owned US sales', () => {
  const c = loadWorker();
  const url = new URL(c.ebaySoldSearchUrl('levis 501 & jeans'));
  assert.equal(url.origin + url.pathname, 'https://www.ebay.com/sch/i.html');
  assert.equal(url.searchParams.get('_nkw'), 'levis 501 & jeans');
  for (const [key, value] of [['LH_Sold', '1'], ['LH_Complete', '1'], ['LH_ItemCondition', '3000'], ['LH_PrefLoc', '1'], ['_ipg', '60']]) {
    assert.equal(url.searchParams.get(key), value, key);
  }
});

function card({ id, title = '', caption = '', price = '', text = '', className = 's-card' }) {
  const parts = { '.s-card__title': title, '.s-card__caption': caption, '.s-card__price': price };
  return { className, dataset: id ? { listingid: id } : {}, innerText: text || `${title} ${price}`,
    querySelector: selector => (parts[selector] ? { innerText: ` ${parts[selector]} ` } : null) };
}

test('cards are read up to the notice where eBay starts matching only some words', () => {
  const c = loadWorker();
  const children = [
    card({ id: '1', title: 'Levi 501', caption: 'Sold Oct 1', price: '$40.00' }),
    card({}),
    card({ id: '2', title: 'Levi 505', price: '$30.00', text: 'Levi 505 Best offer accepted' }),
    card({ id: null, className: 'srp-river-answer REWRITE_START' }),
    card({ id: '3', title: 'Wrangler', price: '$10.00' }),
  ];
  c.document = { querySelector: selector => (selector === 'ul.srp-results' ? { children } : null) };
  const page = c.readEbaySoldCards();
  assert.equal(page.loaded, true);
  assert.deepEqual(JSON.parse(JSON.stringify(page.cards)), [
    { id: '1', title: 'Levi 501', caption: 'Sold Oct 1', price: '$40.00', offer: false },
    { id: '2', title: 'Levi 505', caption: '', price: '$30.00', offer: true },
  ]);
  c.document = { querySelector: () => null };
  assert.deepEqual(JSON.parse(JSON.stringify(c.readEbaySoldCards())), { loaded: false, cards: [] });
});

function browser(c, pages) {
  const listeners = new Set();
  const calls = [];
  c.chrome.tabs = {
    create: async options => { calls.push(['create', options]); return { id: 5 }; },
    update: async (id, options) => {
      calls.push(['update', id, options.url]);
      // The blank page finishing first must not count as the search loading.
      for (const listener of [...listeners]) listener(id, { status: 'complete' }, { url: 'about:blank' });
      for (const listener of [...listeners]) listener(id, { status: 'complete' }, { url: options.url });
      return { id };
    },
    remove: async id => { calls.push(['remove', id]); },
    onUpdated: { addListener: fn => listeners.add(fn), removeListener: fn => listeners.delete(fn) },
  };
  let page = 0;
  c.chrome.scripting = { executeScript: async ({ target, func }) => {
    assert.equal(target.tabId, 5);
    assert.equal(func, c.readEbaySoldCards);
    return [{ result: pages[page++] }];
  } };
  // Only the pause between searches (EBAY_SOLD_PAUSE_MS) runs; the load timeout never fires.
  c.setTimeout = (fn, ms) => { if (ms === 1500) fn(); return 0; };
  return { calls, listeners };
}

test('every search loads in one background tab, which is closed afterwards', async () => {
  const c = loadWorker();
  const b = browser(c, [{ loaded: true, cards: [{ id: '1' }] }, { loaded: true, cards: [] }]);
  const result = JSON.parse(JSON.stringify(await c.readEbaySoldListings(['levis', 'wrangler'])));
  assert.deepEqual(result, { ok: true, results: { levis: [{ id: '1' }], wrangler: [] } });
  assert.deepEqual(b.calls.map(call => call[0]), ['create', 'update', 'update', 'remove']);
  assert.deepEqual({ ...b.calls[0][1] }, { url: 'about:blank', active: false });
  assert.equal(b.calls.at(-1)[1], 5);
  assert.equal(b.listeners.size, 0);
});

test('a page that is not search results ends the batch and keeps what was read', async () => {
  const c = loadWorker();
  const b = browser(c, [{ loaded: true, cards: [{ id: '1' }] }, { loaded: false, cards: [] }, { loaded: true, cards: [] }]);
  const result = JSON.parse(JSON.stringify(await c.readEbaySoldListings(['levis', 'robot', 'never'])));
  assert.deepEqual(result, { ok: false, results: { levis: [{ id: '1' }] }, error: 'eBay did not show search results' });
  assert.equal(b.calls.filter(call => call[0] === 'update').length, 2);
  assert.deepEqual(b.calls.at(-1), ['remove', 5]);
});

test('no queries opens no tab', async () => {
  const c = loadWorker();
  const b = browser(c, []);
  assert.deepEqual(JSON.parse(JSON.stringify(await c.readEbaySoldListings([]))), { ok: true, results: {} });
  assert.deepEqual(b.calls, []);
});
