#!/usr/bin/env node
// Compares Anteroom's Depop form with what depop-sweep.js recorded on Depop's sell page.
// Usage: node vendoo-extension/tools/form-sweep/depop/compare.js <folder with the downloads>
// Exits 1 when anything differs.
const fs = require('node:fs');
const path = require('node:path');
const { readForms } = require('../native-reader');

const folder = process.argv[2];
if (!folder) { console.error('Usage: compare.js <folder>'); process.exit(2); }
const load = name => JSON.parse(fs.readFileSync(path.join(folder, name), 'utf8'));
const fixed = fs.existsSync(path.join(folder, 'depop-fixed.json')) ? load('depop-fixed.json') : {};
const sweep = fs.readdirSync(folder).filter(name => /^depop-sweep-.*\.json$/.test(name)).flatMap(load);
const retired = new Set(sweep.filter(page => page.retired).map(page => `${page.dept}_${page.pt}`));
const keys = [...new Set(sweep.filter(page => page.found).map(page => `${page.dept}_${page.pt}`))];
const [empty, ...forms] = readForms('depop', [{}, ...keys.map(id => ({ direct_category_ids: { depop: id } }))]);
const byId = Object.fromEntries(keys.map((key, index) => [key, forms[index]]));
const build = listing => listing.direct_category_ids?.depop ? byId[listing.direct_category_ids.depop] : empty;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const problems = [];

// Every live category: the fields between Category and Item price, their choices and limits.
let compared = 0;
for (const page of sweep) {
  const key = `${page.dept}_${page.pt}`;
  if (!page.found) {
    if (!retired.has(key)) problems.push({ key, problem: 'not found on the page' });
    continue;
  }
  if (page.retired) { problems.push({ key, problem: 'retired in the catalog but offered on the page' }); continue; }
  compared++;
  const fields = build({ direct_category_ids: { depop: key } }).fields;
  const labels = fields.map(field => field.label);
  const ours = fields.slice(labels.indexOf('Category') + 1, labels.indexOf('Item price')).filter(field => field.input_type !== 'note');
  const theirs = page.fields.filter(field => !['priceAmount', 'shippingMethods'].includes(field.id));
  if (!same(ours.map(field => field.label), theirs.map(field => field.label))) {
    problems.push({ key, problem: 'fields', anteroom: ours.map(field => field.label), depop: theirs.map(field => field.label) });
    continue;
  }
  theirs.forEach((field, index) => {
    const own = ours[index];
    const choices = field.choices || field.options;
    if (choices && !same(own.options, choices)) {
      problems.push({ key, problem: `choices: ${field.label}`,
        onlyAnteroom: own.options.filter(choice => !choices.includes(choice)),
        onlyDepop: choices.filter(choice => !own.options.includes(choice)) });
    }
    const max = field.max === 'two' ? 2 : field.max ? Number(field.max) : null;
    if (field.id.startsWith('attributes.') && max && own.max_values !== max) {
      problems.push({ key, problem: `limit: ${field.label}`, anteroom: own.max_values, depop: max });
    }
  });
}

// The Category list: the page's live categories, case aside (the page uppercases headings).
const base = build({});
const ourCategories = new Set(base.fields.find(field => field.label === 'Category').options.map(name => name.toLowerCase()));
const pageCategories = new Set(sweep.filter(page => page.found).map(page => `${page.header} > ${page.name}`.toLowerCase()));
const onlyOurs = [...ourCategories].filter(name => !pageCategories.has(name));
const onlyPage = [...pageCategories].filter(name => !ourCategories.has(name));
if (onlyOurs.length || onlyPage.length) problems.push({ problem: 'category list', onlyAnteroom: onlyOurs, onlyDepop: onlyPage });

// Choices that do not depend on category.
const byLabel = Object.fromEntries(build({ direct_category_ids: { depop: sweep.find(page => page.found)?.dept + '_' + sweep.find(page => page.found)?.pt } })
  .fields.map(field => [field.label, field]));
for (const [id, label] of [['condition', 'Condition'], ['colour', 'Color'], ['source', 'Source'], ['age', 'Age'], ['style', 'Style']]) {
  if (fixed[id] && !same(byLabel[label]?.options, fixed[id])) problems.push({ problem: `choices: ${label}`, anteroom: byLabel[label]?.options, depop: fixed[id] });
}

console.log(JSON.stringify({ categoriesCompared: compared, categoriesOnPage: pageCategories.size,
  anteroomCategories: ourCategories.size, fixedChoicesChecked: Object.keys(fixed).length,
  mismatches: problems.length, problems }, null, 1));
process.exit(problems.length ? 1 : 0);
