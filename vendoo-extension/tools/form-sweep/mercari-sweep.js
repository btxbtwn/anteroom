// Mercari sell-page sweep. Run on https://www.mercari.com/sell/ while signed in.
// Read-only: it opens the Category and Size pickers and picks categories; it never saves or lists.
//
// It records every list in the category picker (one per category that has subcategories)
// and the Size list for a sample that covers every size group, "All ..." rows and
// categories without sizes. Each run handles the next batch and downloads
// mercari-sweep-<start>.json; the first run also downloads mercari-catalog.json (the
// catalog the page builds from, without brands). Reload the page and run it again until
// it logs "done"; the position is kept in localStorage. Then:
//   node vendoo-extension/tools/form-sweep/mercari-compare.js <folder with the downloads>
// Start over with localStorage.removeItem('anteroomFormSweep:mercari').
(async () => {
  const BATCH = 40;
  const STATE = 'anteroomFormSweep:mercari';
  // Picking categories marks the form unsaved; let the reload between batches go without asking.
  window.addEventListener('beforeunload', event => event.stopImmediatePropagation(), true);
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const text = el => (el?.textContent || '').trim();
  const download = (name, data) => {
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([JSON.stringify(data)], { type: 'application/json' }));
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  const session = await (await fetch('/v1/initialize', { headers: { Accept: 'application/json' } })).json();
  if (!session.csrf || !session.user?.userId) throw new Error('Sign in to Mercari first.');
  const answer = await fetch('/v1/api', { method: 'POST', headers: {
    accept: 'application/json', 'content-type': 'application/json', 'x-csrf-token': session.csrf,
    ...(session.accessToken ? { authorization: `Bearer ${session.accessToken}` } : {}),
    'x-platform': 'web', 'x-app-version': '1', 'x-double-web': '1', 'x-gql-migration': '1', 'apollo-require-preflight': 'true',
  }, body: JSON.stringify({ operationName: 'masterQuery', variables: {}, query: `query masterQuery { master {
    itemCategories { id name parentId itemSizeGroupId displayOrder }
    itemSizeGroups { id title category sizeIds subGroupIds }
    itemSizeSubGroups { id title sizeIds }
    itemSizes { id title subtitle itemSizeGroupId itemSizeSubGroupId displayOrder } } }` }) });
  const master = (await answer.json()).data.master;

  // The picker's tree: top-level categories as listed, subcategories by display order.
  const byId = new Map(master.itemCategories.map(row => [Number(row.id), row]));
  const children = new Map();
  for (const row of master.itemCategories) {
    const parent = byId.has(Number(row.parentId)) ? Number(row.parentId) : 0;
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(row);
  }
  for (const [parent, rows] of children) if (parent) rows.sort((a, b) => Number(a.displayOrder) - Number(b.displayOrder));
  const path = row => {
    const names = [];
    for (let current = row; current; current = byId.get(Number(current.parentId))) names.unshift(current.name);
    return names;
  };
  const ordered = [];
  const visit = row => { ordered.push(row); for (const child of children.get(Number(row.id)) || []) visit(child); };
  for (const row of children.get(0) || []) visit(row);
  const hasChildren = row => (children.get(Number(row.id)) || []).length > 0;
  const selectable = row => !hasChildren(row) || path(row).length > 1;

  const items = [{ kind: 'list', path: [] },
    ...ordered.filter(hasChildren).map(row => ({ kind: 'list', id: Number(row.id), path: path(row) }))];
  const sampled = new Set();
  const sample = row => { if (row && !sampled.has(row.id)) { sampled.add(row.id); items.push({ kind: 'size', id: Number(row.id), path: path(row) }); } };
  for (const group of master.itemSizeGroups) {
    sample(ordered.find(row => selectable(row) && !hasChildren(row) && Number(row.itemSizeGroupId) === Number(group.id)));
    sample(ordered.find(row => selectable(row) && hasChildren(row) && Number(row.itemSizeGroupId) === Number(group.id)));
  }
  for (const top of children.get(0) || []) {
    const below = ordered.filter(row => path(row)[0] === top.name);
    sample(below.find(row => selectable(row) && !hasChildren(row) && !Number(row.itemSizeGroupId)));
    sample(below.find(row => selectable(row) && hasChildren(row) && !Number(row.itemSizeGroupId)));
  }

  const state = JSON.parse(localStorage.getItem(STATE) || '{"next":0}');
  if (state.next >= items.length) { console.log('[form-sweep] done'); return 'done'; }
  if (state.next === 0) download('mercari-catalog.json', { ...master, itemBrands: [] });

  const rowsNow = () => [...document.querySelectorAll('[data-testid=CategoryRow]')];
  const closePicker = async () => {
    const close = [...document.querySelectorAll('[data-testid=DialogBody] button')].find(button => button.getAttribute('aria-label') === 'Close');
    if (close) { close.click(); await sleep(300); }
  };
  // Always from the top of a freshly opened picker.
  const openPicker = async names => {
    await closePicker();
    document.querySelector('[data-testid=SellCategoryFieldButton]').click(); await sleep(600);
    for (const name of names) {
      const row = rowsNow().find(button => text(button) === name);
      if (!row) return false;
      row.click(); await sleep(500);
    }
    return true;
  };

  const start = state.next;
  const results = [];
  for (const item of items.slice(start, start + BATCH)) {
    if (item.kind === 'list') {
      const reached = await openPicker(item.path);
      results.push({ ...item, rows: reached ? rowsNow().map(text) : null });
      await closePicker();
      continue;
    }
    // Pick it: a category with subcategories through its "All ..." row.
    const row = byId.get(item.id);
    const names = hasChildren(row) ? [...item.path, `All ${row.name}`] : item.path;
    let picked = null;
    for (let attempt = 0; attempt < 2 && picked !== item.path.join(' > '); attempt++) {
      if (await openPicker(names)) { await sleep(600); picked = text(document.querySelector('[data-testid=SellCategoryFieldButton]')); }
    }
    if (picked !== item.path.join(' > ')) { await closePicker(); results.push({ ...item, picked }); continue; }
    const input = document.querySelector('[data-testid=Size]');
    let size = null;
    if (input) {
      input.click(); input.focus(); await sleep(500);
      size = [];
      let heading = '';
      for (const li of document.querySelectorAll('#itemSizeId > li')) {
        if (li.dataset.testid === 'Size-option') size.push({ heading, option: text(li) });
        else heading = text(li);
      }
      input.blur(); document.body.click(); await sleep(200);
    }
    results.push({ ...item, picked, size });
  }
  download(`mercari-sweep-${start}.json`, results);
  localStorage.setItem(STATE, JSON.stringify({ next: start + BATCH }));
  const left = Math.max(0, items.length - start - BATCH);
  console.log(`[form-sweep] saved ${start}-${start + results.length - 1}; ${left} left. Reload and run again.`);
  return left ? `saved ${start}; ${left} left` : 'done';
})();
