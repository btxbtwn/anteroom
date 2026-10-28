// Run in a signed-in tab on https://poshmark.com/create-listing, one call per
// page load (reload between departments; the page slows down):
//   await poshmarkSweep(['Men'])   // then 'Women', ['Kids', 'Pets'], ['Home', 'Electronics']
// Picks every category, subcategory and grouped category, opens each Size tab,
// and downloads poshmark-sweep-<departments>.json. Read-only: it never clicks
// Next, Save or anything that creates a listing. Also download the catalogs:
//   await poshmarkSweepCatalogs()
// then run compare.js against the files.
async function poshmarkSweep(DEPTS) {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const txt = e => (e.textContent || '').trim();
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const dds = () => [...document.querySelectorAll('[data-test=dropdown]')];
  const catDD = () => dds()[1], subDD = () => dds()[2], sizeDD = () => dds()[3];
  const toggle = async d => { const c = d.querySelector('[data-test=dropdown-container]'); (c.querySelector('span,div') || c).click(); await sleep(450); };
  const open = async d => { if (d.getAttribute('aria-expanded') !== 'true') await toggle(d); };
  const close = async d => { if (d.getAttribute('aria-expanded') === 'true') await toggle(d); };
  const items = d => [...d.querySelectorAll('li')].filter(vis);
  const readSizes = async () => {
    const d = sizeDD(); if (!d || !vis(d)) return null;
    await open(d);
    const tabs = [...d.querySelectorAll('li[data-test^=horizontal-]')].filter(vis);
    // The size menu opens on US. `sizes` is that US list; AU/EU/UK are the same tabs
    // with the size-system switch, checked from the catalog rather than another pass here.
    const sizes = () => [...d.querySelectorAll('.multi-size-selector__button')].filter(vis).map(txt);
    const out = [];
    for (const t of tabs) { (t.querySelector('a') || t).click(); await sleep(300); out.push({ tab: txt(t), sizes: txt(t) === 'Custom' ? [] : sizes() }); }
    if (!tabs.length) out.push({ tab: '', sizes: sizes() });
    await close(d);
    return out;
  };
  const pickOnce = async (dept, steps) => {
    await open(catDD());
    const all = [...catDD().querySelectorAll('a[data-et-name="all"]')].find(vis); if (all) { all.click(); await sleep(400); }
    await open(catDD());
    const da = [...catDD().querySelectorAll('a[data-et-name]')].find(a => vis(a) && txt(a) === dept);
    if (!da) return 'no department';
    da.click(); await sleep(450);
    for (const step of steps) {
      await open(catDD());
      const li = items(catDD()).find(x => txt(x) === step);
      if (!li) return 'missing ' + step;
      li.click(); await sleep(650);
    }
    return '';
  };
  const pick = async (dept, steps) => {
    let err = await pickOnce(dept, steps);
    for (let i = 0; err && i < 2; i++) { await close(catDD()); await close(subDD()); document.body.click(); await sleep(400); err = await pickOnce(dept, steps); }
    return err;
  };
  const finish = async row => {
    const sd = subDD();
    await open(sd);
    row.subs = items(sd).map(txt);
    if (row.subs.length) { items(sd)[0].click(); await sleep(450); } else await close(sd);
    row.sizes = await readSizes();
  };
  const result = { departments: [] };
  for (const dept of DEPTS) {
    const deptOut = { dept, categories: [] };
    result.departments.push(deptOut);
    if (await pick(dept, [])) { deptOut.error = 'no department'; continue; }
    await open(catDD());
    const cats = items(catDD()).map(txt).filter(t => t !== 'All Categories' && t !== dept);
    deptOut.order = cats;
    for (const cat of cats) {
      const err = await pick(dept, [cat]);
      if (err) { deptOut.categories.push({ cat, error: err }); continue; }
      const nested = catDD().getAttribute('aria-expanded') === 'true' ? items(catDD()).map(txt) : null;
      if (nested && nested.includes(cat)) {
        // A group such as Global & Traditional Wear: its categories are one step deeper.
        const children = nested.filter(t => t !== 'All Categories' && t !== dept && t !== cat);
        deptOut.categories.push({ cat, group: children });
        for (const child of children) {
          const row = { cat: `${cat} > ${child}`, parent: cat, child };
          const e2 = await pick(dept, [cat, child]);
          if (e2) row.error = e2; else await finish(row);
          deptOut.categories.push(row);
        }
        continue;
      }
      const row = { cat };
      await finish(row);
      deptOut.categories.push(row);
    }
  }
  download(`poshmark-sweep-${DEPTS.join('-')}.json`, JSON.stringify(result));
  return result;
}

async function poshmarkSweepCatalogs() {
  for (const [name, path] of [['catalog', '/vm-rest/meta/catalog_v2'], ['display', '/vm-rest/meta/catalog_display']]) {
    download(`poshmark-${name}.json`, await (await fetch(path, { credentials: 'omit' })).text());
  }
}

function download(name, text) {
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  link.download = name; document.body.appendChild(link); link.click(); link.remove();
}
