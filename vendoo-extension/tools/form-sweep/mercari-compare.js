#!/usr/bin/env node
// Compares Anteroom's Mercari form with what mercari-sweep.js recorded on Mercari's sell page.
// Usage: node vendoo-extension/tools/form-sweep/mercari-compare.js <folder with the downloads>
// Exits 1 when anything differs.
const fs = require('node:fs');
const path = require('node:path');
const { loadWorker, call } = require('../../tests/worker-context');

const folder = process.argv[2];
if (!folder) { console.error('Usage: mercari-compare.js <folder>'); process.exit(2); }
const load = name => JSON.parse(fs.readFileSync(path.join(folder, name), 'utf8'));
const master = load('mercari-catalog.json');
const sweep = fs.readdirSync(folder).filter(name => /^mercari-sweep-.*\.json$/.test(name)).flatMap(load);
const worker = loadWorker();
const build = listing => JSON.parse(JSON.stringify(call(worker, 'mercariEditorForm', listing, master)));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const fold = value => String(value ?? '').toLowerCase();
const problems = [];

// Each picker list: Anteroom's Category choices below that path, then its "All ..." row.
const choices = build({}).fields.find(field => field.label === 'Category').options;
const lists = sweep.filter(item => item.kind === 'list');
for (const list of lists) {
  if (!list.rows) { problems.push({ path: list.path, problem: 'list not reached on the page' }); continue; }
  const prefix = list.path.join(' > ');
  // Top-level categories are not choices themselves, only their subcategories are. Below the
  // top, every row is a choice (a category with subcategories through its "All ..." row).
  const below = list.path.length
    ? choices.filter(label => label.startsWith(`${prefix} > `) && label.split(' > ').length === list.path.length + 1)
      .map(label => label.split(' > ').at(-1))
    : [...new Set(choices.map(label => label.split(' > ')[0]))];
  const expected = [...below, ...(list.path.length > 1 && choices.includes(prefix) ? [`All ${list.path.at(-1)}`] : [])];
  if (!same(expected, list.rows)) {
    problems.push({ path: prefix || '(top)', problem: 'category list',
      onlyAnteroom: expected.filter(row => !list.rows.includes(row)), onlyMercari: list.rows.filter(row => !expected.includes(row)),
      sameRowsDifferentOrder: expected.length === list.rows.length && expected.every(row => list.rows.includes(row)) });
  }
}

// Size for the sampled categories: presence, choices in order, and their headings.
const sizes = sweep.filter(item => item.kind === 'size');
for (const item of sizes) {
  const key = item.path.join(' > ');
  if (item.picked !== key) { problems.push({ category: key, problem: 'could not pick it on the page', shown: item.picked }); continue; }
  const field = build({ direct_category_ids: { mercari: String(item.id) } }).fields.find(row => row.label.replace(/\s*\*$/, '') === 'Size');
  if (!field !== !item.size) { problems.push({ category: key, problem: 'size field', anteroom: Boolean(field), mercari: Boolean(item.size) }); continue; }
  if (!field) continue;
  // Anteroom marks a size name shared by two groups with " · Group" so both stay pickable.
  const ours = field.options.map(label => ({ heading: fold(field.option_groups?.[label]), option: label.replace(/ · .*$/, '') }));
  const theirs = item.size.map(row => ({ heading: fold(row.heading), option: row.option }));
  if (!same(ours, theirs)) problems.push({ category: key, problem: 'size choices', anteroom: ours.slice(0, 8), mercari: theirs.slice(0, 8) });
}

console.log(JSON.stringify({ pickerLists: lists.length, sizeSamples: sizes.length, mismatches: problems.length, problems }, null, 1));
process.exit(problems.length ? 1 : 0);
