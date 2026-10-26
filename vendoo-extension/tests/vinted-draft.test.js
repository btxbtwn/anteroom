const test = require('node:test');
const assert = require('node:assert/strict');
const { classifyVintedControl, vintedCategoryPath, vintedConditionCandidates, vintedListControl } = require('../content-scripts/vinted.js');

test('Upload is the list control, and Save draft is not', () => {
  assert.equal(vintedListControl('Upload'), true);
  assert.equal(vintedListControl('Publish'), true);
  assert.equal(vintedListControl('Upload item'), true);
  assert.equal(vintedListControl('Save draft'), false);
  assert.equal(vintedListControl('Save as draft'), false);
  assert.equal(vintedListControl('List this item'), false);
  assert.equal(vintedListControl('Save'), false);
  assert.equal(classifyVintedControl('Save draft'), 'save');
  assert.equal(classifyVintedControl(''), 'ignore');
});

test('the Vinted category is the direct breadcrumb, not the Vendoo category', () => {
  const data = {
    vinted_specifics: { category: 'Women > Clothing > Jeans > Straight jeans' },
    marketplace_categories: { vinted: '11450' },
  };
  assert.equal(vintedCategoryPath(data), 'Women > Clothing > Jeans > Straight jeans');
  assert.equal(vintedCategoryPath({ marketplace_categories: { vinted: '11450' } }), '');
  assert.equal(vintedCategoryPath({}), '');
});

test('Vinted condition labels try the live form wording', () => {
  assert.deepEqual(vintedConditionCandidates('Very good'), ['Very good']);
  assert.deepEqual(vintedConditionCandidates('New without tags'), ['New without tags', 'Like new']);
  assert.deepEqual(vintedConditionCandidates(''), []);
});
