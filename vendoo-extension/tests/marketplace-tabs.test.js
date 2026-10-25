const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker } = require('./worker-context');

test('marketplace tabs select an existing normal Chrome window explicitly', async () => {
  const c = loadWorker();
  c.chrome.windows.getAll = async filter => {
    assert.deepEqual([...filter.windowTypes], ['normal']);
    return [{ id: 4 }, { id: 7, focused: true }];
  };
  c.chrome.tabs = { create: async options => {
    assert.equal(options.windowId, 7);
    assert.equal(options.url, 'https://www.depop.com/sellinghub/drafts/');
    assert.equal(options.active, true);
    return { id: 8 };
  } };
  assert.equal((await c.openMarketplaceTab('https://www.depop.com/sellinghub/drafts/')).id, 8);
});

test('no Chrome windows opens one blank window then navigates once', async () => {
  const c = loadWorker();
  c.chrome.windows.getAll = async () => [];
  let created = 0;
  let navigated = 0;
  c.chrome.tabs = { create: async () => { assert.fail('No current window'); } };
  c.createWindowSafe = async options => {
    created += 1;
    assert.equal(options.url, 'about:blank');
    assert.equal(options.focused, true);
    return { tabs: [{ id: 9 }] };
  };
  c.chrome.tabs.update = async (id, options) => {
    navigated += 1;
    assert.equal(id, 9);
    assert.equal(options.url, 'https://poshmark.com/create-listing');
    return { id };
  };
  await c.openMarketplaceTab('https://poshmark.com/create-listing');
  assert.equal(created, 1);
  assert.equal(navigated, 1);
});

test('failed navigation never creates another window or repeats the navigation', async () => {
  const c = loadWorker();
  c.chrome.windows.getAll = async () => [];
  let created = 0;
  let navigated = 0;
  c.createWindowSafe = async () => { created += 1; return { tabs: [{ id: 9 }] }; };
  c.chrome.tabs = { update: async () => { navigated += 1; throw new Error('Navigation interrupted'); } };
  await assert.rejects(c.openMarketplaceTab('https://poshmark.com/create-listing'), /Navigation interrupted/);
  assert.equal(created, 1);
  assert.equal(navigated, 1);
});

test('blank and background marketplace tabs preserve their requested behavior', async () => {
  const c = loadWorker();
  c.chrome.windows.getAll = async () => [];
  c.createWindowSafe = async options => {
    assert.equal(options.focused, false);
    return { tabs: [{ id: 9 }] };
  };
  c.chrome.tabs = { update: async () => { assert.fail('Blank tabs must not navigate again'); } };
  assert.equal((await c.openMarketplaceTab('about:blank', { active: false })).id, 9);
});
