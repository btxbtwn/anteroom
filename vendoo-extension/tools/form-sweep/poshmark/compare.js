// node vendoo-extension/tools/form-sweep/poshmark/compare.js <dir>
// <dir> holds poshmark-catalog.json, poshmark-display.json and the sweep files (names starting
// with poshmark-sweep-). Exits 1 when Anteroom's Poshmark form differs from the page.
const path = require('node:path'); const fs = require('node:fs');
const [dir] = process.argv.slice(2);
const { readForms } = require('../native-reader');
const catalog = JSON.parse(fs.readFileSync(path.join(dir, 'poshmark-catalog.json'), 'utf8'));
const display = JSON.parse(fs.readFileSync(path.join(dir, 'poshmark-display.json'), 'utf8')).data.us.catalog_display;
const depts = fs.readdirSync(dir).filter(f => f.startsWith('poshmark-sweep-')).flatMap(f => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')).departments);
const byName = Object.fromEntries(depts.map(d => [d.dept, d]));
const norm = s => s.replace(/\s+/g, ' ').trim();
const [base] = readForms('poshmark', [{}]);
const ours = base.fields.find(f => f.path === 'direct_category_ids.poshmark');
const categoryIds = [...new Set(Object.values(ours.option_values).map(id => id.split('/').slice(0, 2).join('/')))];
const nativeForms = readForms('poshmark', categoryIds.map(id => ({ direct_category_ids: { poshmark: id } })));
const byId = Object.fromEntries(categoryIds.map((id, index) => [id, nativeForms[index]]));
// The page's list, in its order.
const page = [];
const pageRows = [];
for (const d of display.children) {
  const dept = catalog.catalog.departments.find(x => x.id === d.id).display;
  for (const row of byName[dept].categories) {
    if (row.group) continue;
    const parts = row.parent ? [dept, row.parent, row.child] : [dept, row.cat];
    const real = (row.subs || []).filter(s => s !== 'None');
    if (!real.length) page.push(parts.join(' > '));
    else for (const s of row.subs) page.push([...parts, s].join(' > '));
    pageRows.push({ dept, parts, row });
  }
}
const problems = [];
if (JSON.stringify(ours.options) !== JSON.stringify(page)) {
  const a = new Set(ours.options), b = new Set(page);
  problems.push({ problem: 'category list', anteroom: ours.options.length, page: page.length,
    onlyAnteroom: ours.options.filter(x => !b.has(x)).slice(0, 10), onlyPage: page.filter(x => !a.has(x)).slice(0, 10),
    firstOrderDiff: ours.options.findIndex((x, i) => x !== page[i]) });
}
let sized = 0;
for (const { parts, row } of pageRows) {
  const label = parts.join(' > ');
  const pick = ours.options.find(x => x === label || x === `${label} > None`);
  if (!pick) continue;
  const form = byId[ours.option_values[pick].split('/').slice(0, 2).join('/')];
  const inv = form.fields.find(f => f.input_type === 'inventory');
  const got = inv.inventory_groups.map(g => ({ tab: g.label, sizes: g.id === 'custom' ? [] : (g.systems?.us || g.sizes).map(s => norm(s.label)) }));
  const want = (row.sizes || []).map(t => ({ tab: t.tab, sizes: t.sizes.map(norm) }));
  sized++;
  if (JSON.stringify(got) !== JSON.stringify(want)) problems.push({ problem: 'sizes', category: label, anteroom: got.map(g => `${g.tab}:${g.sizes.length}`), page: want.map(g => `${g.tab}:${g.sizes.length}`) });
}
console.log(JSON.stringify({ categories: page.length, sizeChecked: sized, problems: problems.length, list: problems.slice(0, 15) }, null, 1));
process.exit(problems.length ? 1 : 0);
