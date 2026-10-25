const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const src = fs.readFileSync(path.join(__dirname, '../content-scripts/vendoo.js'), 'utf8');

function extract(name) {
  const start = src.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, name);
  let depth = 0;
  let seen = false;
  for (let i = src.indexOf('{', start); i < src.length; i += 1) {
    if (src[i] === '{') {
      depth += 1;
      seen = true;
    } else if (src[i] === '}') {
      depth -= 1;
      if (seen && depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`unclosed ${name}`);
}

const context = {};
vm.runInNewContext(
  `${extract('normalizeText')}\n${extract('normalizeEtsyWhenMade')}\nthis.normalizeEtsyWhenMade = normalizeEtsyWhenMade;`,
  context,
);

test('non-vintage Etsy when-made selects a year range', () => {
  const select = context.normalizeEtsyWhenMade;
  assert.equal(select('Made To Order (Not Yet Made)'), '2010 - 2019 (Recently)');
  assert.equal(select('made_to_order'), '2010 - 2019 (Recently)');
  assert.equal(select('not yet made'), '2010 - 2019 (Recently)');
  assert.equal(select('non-vintage'), '2010 - 2019 (Recently)');
  assert.equal(select('not vintage'), '2010 - 2019 (Recently)');
  assert.equal(select('1990s'), '1990s (Vintage)');
  assert.equal(select('2024'), '2020 - 2026 (Recently)');
});
