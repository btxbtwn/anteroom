const test = require('node:test');
const assert = require('node:assert/strict');
const { problem } = require('../tools/version-bump/check');

test('a Bridge code change needs a higher manifest version', () => {
  assert.match(problem(['vendoo-extension/background/marketplace-promotions.js'], '0.2.55', '0.2.55'), /still 0\.2\.55/);
  assert.match(problem(['vendoo-extension/background.js'], '0.2.55', '0.2.54'), /Raise its version above 0\.2\.55/);
  assert.equal(problem(['vendoo-extension/background.js', 'vendoo-extension/manifest.json'], '0.2.55', '0.2.56'), null);
  assert.equal(problem(['vendoo-extension/background.js'], '0.2.55', '0.3.0'), null);
});

test('tests, tools and docs change nothing Chrome runs', () => {
  assert.equal(problem(['vendoo-extension/tests/background.test.js', 'vendoo-extension/tools/form-sweep/depop/sweep.js',
    'vendoo-extension/README.md'], '0.2.55', '0.2.55'), null);
  assert.equal(problem([], '0.2.55', '0.2.55'), null);
});
