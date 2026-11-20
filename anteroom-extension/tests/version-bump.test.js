const test = require('node:test');
const assert = require('node:assert/strict');
const { problem } = require('../tools/version-bump/check');

test('a Bridge code change needs a higher manifest version', () => {
  assert.match(problem(['anteroom-extension/background/marketplace-promotions.js'], '0.2.55', '0.2.55'), /still 0\.2\.55/);
  assert.match(problem(['anteroom-extension/background.js'], '0.2.55', '0.2.54'), /Raise its version above 0\.2\.55/);
  assert.equal(problem(['anteroom-extension/background.js', 'anteroom-extension/manifest.json'], '0.2.55', '0.2.56'), null);
  assert.equal(problem(['anteroom-extension/background.js'], '0.2.55', '0.3.0'), null);
});

test('tests, tools, dotfiles and docs change nothing Chrome runs', () => {
  assert.equal(problem(['anteroom-extension/tests/background.test.js', 'anteroom-extension/tools/form-sweep/depop/sweep.js',
    'anteroom-extension/.oxlintrc.json', 'anteroom-extension/README.md'], '0.2.55', '0.2.55'), null);
  assert.equal(problem([], '0.2.55', '0.2.55'), null);
});
