// Depop sell-page sweep. Run on https://www.depop.com/products/create/ while signed in.
// Read-only: it types into the Category search and opens dropdowns; it never saves or posts.
//
// Each run sweeps the next batch of categories and downloads depop-sweep-<start>.json.
// The first run also downloads depop-catalog.json (the catalog the page builds from) and
// depop-fixed.json (choices that do not depend on category). Reload the page and run it
// again until it logs "done"; the position is kept in localStorage. Then:
//   node vendoo-extension/tools/form-sweep/depop-compare.js <folder with the downloads>
// Start over with localStorage.removeItem('anteroomFormSweep:depop').
(async () => {
  const BATCH = 40;
  const STATE = 'anteroomFormSweep:depop';
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
  const read = async path => {
    const token = (document.cookie.match(/(?:^|; )access_token=([^;]+)/) || [])[1];
    if (!token) throw new Error('Sign in to Depop first.');
    const answer = await fetch(`https://webapi.depop.com${path}`, { credentials: 'omit',
      headers: { Accept: 'application/json', Authorization: `Bearer ${decodeURIComponent(token)}` } });
    return answer.json();
  };
  const usName = row => row?.name_i18n?.['en-US'] ?? row?.name_i18n?.en ?? row?.name ?? row?.id;
  const setInput = (input, value) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  };
  const openChoices = async id => {
    const toggle = document.getElementById(`${id}-toggle-button`);
    if (!toggle) return null;
    toggle.click(); await sleep(250);
    const choices = [...document.querySelectorAll(`[id^="${id}-item-"]`)].map(text);
    toggle.click(); await sleep(100);
    return choices;
  };

  const attributes = await read('/presentation/api/v1/attributes/');
  const departments = Object.fromEntries(attributes.department.map(row => [row.id, usName(row)]));
  const pairs = [];
  for (const group of attributes.group) {
    for (const product of group.product_types || []) {
      for (const department of product.department || group.department || []) {
        pairs.push({ dept: department, group: group.id, pt: product.id, name: usName(product),
          header: `${departments[department]} > ${usName(group)}`.toLowerCase(),
          retired: group.status === 'inactive' || product.status === 'inactive' });
      }
    }
  }
  const state = JSON.parse(localStorage.getItem(STATE) || '{"next":0}');
  if (state.next >= pairs.length) { console.log('[form-sweep] done'); return 'done'; }

  if (state.next === 0) {
    const sizes = await read('/presentation/api/v1/attributes/categories/size-mapping/');
    download('depop-catalog.json', { ...attributes, brand: [], brands: [], popular_brands: [],
      category_size_mapping: sizes.category_size_mapping, size_sets: sizes.size_sets });
    const fixed = {};
    for (const id of ['condition', 'colour', 'source', 'age', 'style']) fixed[id] = await openChoices(id);
    download('depop-fixed.json', fixed);
  }

  const start = state.next;
  const results = [];
  for (const pair of pairs.slice(start, start + BATCH)) {
    if (pair.retired) { results.push({ ...pair, found: false }); continue; }
    const input = document.getElementById('group-input');
    // A different search first, so the same name typed twice still reopens the list.
    input.focus(); setInput(input, 'qqqq'); await sleep(200); setInput(input, pair.name); await sleep(500);
    let target = null;
    let header = '';
    const list = document.querySelector('[id^=group-item-]')?.closest('[role=listbox]');
    for (const el of list ? list.querySelectorAll('li, p, span, div') : []) {
      if (el.getAttribute('role') === 'option') {
        if (header === pair.header && text(el) === pair.name) { target = el; break; }
      } else if (el.children.length === 0 && text(el).includes(' > ')) header = text(el).toLowerCase();
    }
    if (!target) { results.push({ ...pair, found: false }); input.blur(); continue; }
    target.click(); await sleep(600);
    const fields = [];
    for (const label of document.querySelectorAll('main label[id$="-label"], main label[id$="__input-label"]')) {
      const id = label.id.replace(/(-label|__input-label)$/, '');
      if (['group', 'upload-input', 'searchBar'].includes(id)) continue;
      const max = (text(label.parentElement?.parentElement || label).match(/up to (\w+)/i) || [])[1] || null;
      const row = { id, label: (label.innerText || text(label)).split('\n')[0].trim(), max };
      if (id.startsWith('attributes.') || id === 'variants') row.choices = await openChoices(id);
      fields.push(row);
    }
    results.push({ ...pair, found: true, fields });
  }
  download(`depop-sweep-${start}.json`, results);
  localStorage.setItem(STATE, JSON.stringify({ next: start + BATCH }));
  const left = Math.max(0, pairs.length - start - BATCH);
  console.log(`[form-sweep] saved ${start}-${start + results.length - 1}; ${left} left. Reload and run again.`);
  return left ? `saved ${start}; ${left} left` : 'done';
})();
