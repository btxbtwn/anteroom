const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, call } = require('./worker-context');

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

test('new windows open on a visible display, sized from a visible window', () => {
  const c = loadWorker();
  const displays = [{ isPrimary: true, isEnabled: true, workArea: { left: 1920, top: 25, width: 1512, height: 945 } }];
  const offscreen = { id: 1, left: -20000, top: 0, width: 1280, height: 900, state: 'normal' };
  const bounds = call(c, 'pickOnscreenBounds', [offscreen], displays);
  assert.ok(bounds.left >= 1920 && bounds.left + bounds.width <= 1920 + 1512);
  assert.ok(bounds.top >= 25);
  assert.deepEqual(call(c, 'pickOnscreenBounds', [], []), { left: 80, top: 80, width: 800, height: 600 });
  const visible = call(c, 'pickOnscreenBounds', [offscreen, { id: 2, left: 120, top: 40, width: 1440, height: 800, state: 'normal' }], []);
  assert.deepEqual(visible, { left: 120, top: 40, width: 1280, height: 800 });
});

test('a window Chrome refuses for its bounds is retried without them', async () => {
  const c = loadWorker();
  const attempts = [];
  c.chrome.system = {};
  c.chrome.windows.getAll = async () => [];
  c.chrome.windows.create = async (options) => {
    attempts.push(options);
    if (attempts.length === 1) throw new Error('Bounds must be at least 50% within visible screen space.');
    return { id: 3, tabs: [{ id: 4 }] };
  };
  assert.equal((await c.createWindowSafe({ focused: false })).id, 3);
  assert.equal(attempts.length, 2);
  assert.equal(attempts[1].left, 0);
  c.chrome.windows.create = async () => { throw new Error('No window with id: 9'); };
  await assert.rejects(c.createWindowSafe({}), /No window/);
});
